from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AgentRun, Conversation, ConversationMessage
from app.services.model_provider import ProviderError, ProviderResponse, ProviderToolCall, ProviderUsage

from conftest import create_project


class FakeProvider:
    def __init__(self, responses=None, *, delay: float = 0):
        self.responses = list(responses or [])
        self.calls: list[dict] = []
        self.delay = delay
        self.configured = True

    async def complete(self, messages, tools, max_completion_tokens):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools, "max_completion_tokens": max_completion_tokens})
        if self.delay:
            await asyncio.sleep(self.delay)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def test_connection(self):
        return ProviderResponse(
            content="ready",
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            request_id="fake-test",
        )


def final_response(content="Done"):
    return ProviderResponse(
        content=content,
        tool_calls=[],
        finish_reason="stop",
        usage=ProviderUsage(prompt_tokens=8, completion_tokens=5, total_tokens=13),
        request_id="fake-response",
    )


def tool_response(*calls):
    return ProviderResponse(
        content="",
        tool_calls=[ProviderToolCall(id=call_id, name=name, arguments=json.dumps(arguments)) for call_id, name, arguments in calls],
        finish_reason="tool_calls",
        usage=ProviderUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14),
        request_id="fake-tool-response",
    )


def wait_for_terminal(client, project_id, run_id, timeout=2):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = client.get(f"/api/projects/{project_id}/runs/{run_id}").json()
        if latest["status"] in {"completed", "failed", "cancelled", "interrupted", "incomplete"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"run did not finish: {latest}")


def configured_app(app_settings, provider):
    app = create_app(app_settings)
    app.state.model_provider = provider
    app.state.model_connection_status = "configured_not_tested"
    return app


def test_config_status_is_sanitized_and_connection_test_is_explicit(client):
    status = client.get("/api/config/model")
    assert status.status_code == 200
    assert status.json()["status"] == "not_configured"
    assert "MODEL_API_KEY" not in status.text
    assert client.post("/api/config/model/test").status_code == 503


def test_plain_completion_and_conversation_persist(app_settings):
    app = configured_app(app_settings, FakeProvider([final_response("Ask me about the saved circuit.")]))
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        submitted = client.post(
            f"/api/projects/{project_id}/conversation/messages",
            json={"content": "What should I clarify next?"},
        )
        assert submitted.status_code == 202, submitted.text
        run = wait_for_terminal(client, project_id, submitted.json()["id"])
        assert run["status"] == "completed"
        assert run["usage"]["total_tokens"] == 13
        conversation = client.get(f"/api/projects/{project_id}/conversation").json()
        assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
        assert conversation["messages"][1]["content"] == "Ask me about the saved circuit."

    restarted = create_app(app_settings)
    with TestClient(restarted) as client:
        recovered = client.get(f"/api/projects/{project_id}/conversation").json()
        assert len(recovered["messages"]) == 2
        assert recovered["latest_run"]["status"] == "completed"


def test_tool_call_order_and_project_binding(app_settings):
    provider = FakeProvider(
        [
            tool_response(("call-1", "get_project_requirements", {})),
            final_response("I read the saved requirements."),
        ]
    )
    app = configured_app(app_settings, provider)
    with TestClient(app) as client:
        project = create_project(client, requirements="Use a synchronous reset.")
        project_id = project["id"]
        client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Read my requirements."})
        run = wait_for_terminal(client, project_id, client.get(f"/api/projects/{project_id}/conversation").json()["latest_run"]["id"])
        assert run["status"] == "completed"
        second_messages = provider.calls[1]["messages"]
        assistant = next(message for message in second_messages if message.get("role") == "assistant" and message.get("tool_calls"))
        tool = next(message for message in second_messages if message.get("role") == "tool")
        assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "call-1"
        assert json.loads(tool["content"])["requirements"] == "Use a synchronous reset."

        with app.state.session_factory() as session:
            protocol = session.query(ConversationMessage).filter(ConversationMessage.project_id == project_id, ConversationMessage.visibility == "protocol").all()
            assert any(message.role == "assistant" and message.protocol_json.get("tool_calls") for message in protocol)
            assert any(message.role == "tool" and message.tool_call_id == "call-1" for message in protocol)


def test_multiple_tools_and_invalid_arguments_are_structured(app_settings):
    provider = FakeProvider(
        [
            tool_response(
                ("call-1", "list_project_files", {}),
                ("call-2", "read_project_file", {"path": "../escape.sv"}),
            ),
            final_response("The file read was rejected safely."),
        ]
    )
    app = configured_app(app_settings, provider)
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        response = client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Inspect the project."})
        run = wait_for_terminal(client, project_id, response.json()["id"])
        assert run["status"] == "completed"
        tool_messages = [message for message in provider.calls[1]["messages"] if message.get("role") == "tool"]
        assert len(tool_messages) == 2
        assert json.loads(tool_messages[1]["content"])["error"]["code"] == "invalid_file_path"

    # An argument attempting to supply another project is rejected by the typed schema.
    with app.state.session_factory() as session:
        assert app.state.tool_registry.execute(
            "read_project_file",
            {"path": "main.sv", "project_id": "other"},
            project_id=project_id,
            session=session,
            workspace=app.state.workspace,
        )["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("code", ["authentication_failed", "access_restricted", "rate_limited", "provider_timeout", "connection_failed"])
def test_provider_failures_are_safe_and_retryable(app_settings, code):
    app = configured_app(app_settings, FakeProvider([ProviderError(code, f"safe {code}")]))
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        submitted = client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Try it."})
        run = wait_for_terminal(client, project_id, submitted.json()["id"])
        assert run["status"] == "failed"
        assert run["error_code"] == code
        assert "safe" in run["error_message"]
        assert "MODEL_API_KEY" not in client.get(f"/api/projects/{project_id}/runs/{run['id']}").text


def test_empty_provider_response_is_reported_as_malformed(app_settings):
    empty = ProviderResponse(
        content="",
        tool_calls=[],
        finish_reason="stop",
        usage=ProviderUsage(),
        request_id="fake-empty",
    )
    app = configured_app(app_settings, FakeProvider([empty]))
    with TestClient(app) as client:
        project_id = create_project(client)["id"]
        submitted = client.post(
            f"/api/projects/{project_id}/conversation/messages",
            json={"content": "Return an answer."},
        )
        run = wait_for_terminal(client, project_id, submitted.json()["id"])
        assert run["status"] == "failed"
        assert run["error_code"] == "malformed_provider_response"


def test_round_and_tool_limits_record_incomplete_outcome(app_settings):
    settings = app_settings.__class__(**{**app_settings.__dict__, "max_model_rounds": 1, "max_tool_calls": 1})
    provider = FakeProvider([tool_response(("call-1", "list_project_files", {}), ("call-2", "list_project_files", {}))])
    app = configured_app(settings, provider)
    with TestClient(app) as client:
        project = create_project(client)
        response = client.post(f"/api/projects/{project['id']}/conversation/messages", json={"content": "List files."})
        run = wait_for_terminal(client, project["id"], response.json()["id"])
        assert run["status"] == "incomplete"
        assert run["tool_calls"] == 1
        assert "limit" in run["events"][-2]["summary"].lower() or "limit" in run["events"][-1]["summary"].lower()
        assert len(provider.calls) == 1


def test_cancellation_and_duplicate_active_submission(app_settings):
    provider = FakeProvider([final_response()], delay=5)
    app = configured_app(app_settings, provider)
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        first = client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Wait for me."})
        second = client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Duplicate turn."})
        assert second.status_code == 409
        cancel = client.post(f"/api/projects/{project_id}/runs/{first.json()['id']}/cancel")
        assert cancel.status_code == 200
        run = wait_for_terminal(client, project_id, first.json()["id"])
        assert run["status"] == "cancelled"


def test_connection_test_and_retry(app_settings):
    provider = FakeProvider([ProviderError("rate_limited", "try later"), final_response("Recovered")])
    app = configured_app(app_settings, provider)
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        test = client.post("/api/config/model/test")
        assert test.status_code == 200
        assert test.json()["status"] == "connected"
        submitted = client.post(f"/api/projects/{project_id}/conversation/messages", json={"content": "Retry me."})
        failed = wait_for_terminal(client, project_id, submitted.json()["id"])
        assert failed["status"] == "failed"
        retried = client.post(f"/api/projects/{project_id}/runs/{failed['id']}/retry")
        assert retried.status_code == 202
        completed = wait_for_terminal(client, project_id, retried.json()["id"])
        assert completed["status"] == "completed"
        assert client.get("/api/config/model").json()["status"] == "connected"


def test_recover_interrupted_run_after_restart(app_settings):
    app = create_app(app_settings)
    with TestClient(app) as client:
        project_id = create_project(client)["id"]
    with app.state.session_factory() as session:
        conversation = Conversation(project_id=project_id)
        session.add(conversation)
        session.flush()
        message = ConversationMessage(
            conversation_id=conversation.id,
            project_id=project_id,
            ordinal=1,
            role="user",
            visibility="user",
            content="Interrupted",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            project_id=project_id,
            conversation_id=conversation.id,
            user_message_id=message.id,
            status="running",
            model="muse-spark-1.3",
        )
        session.add(run)
        session.commit()
        run_id = run.id
    restarted = create_app(app_settings)
    with TestClient(restarted) as client:
        recovered = client.get(f"/api/projects/{project_id}/runs/{run_id}").json()
        assert recovered["status"] == "interrupted"
