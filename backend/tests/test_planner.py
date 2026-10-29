from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.model_provider import ProviderResponse, ProviderToolCall, ProviderUsage
from app.services.planner_service import MetaRequirementsPlanner, PlannerContractError, build_planner_context, parse_planner_response
from app.specification import CircuitSpecification, PlannerProposal, ValidationReport

from conftest import create_project
from test_specification import base_spec, save_spec


def planner_proposal() -> PlannerProposal:
    specification = CircuitSpecification.model_validate(
        {
            **base_spec(),
            "circuit_name": "planned_data_path",
            "purpose": "Move a bounded value from input to output using a reviewed planner proposal.",
            "original_request": "Build a small data path.",
        }
    )
    return PlannerProposal(
        proposed_specification=specification,
        assumptions=[],
        clarification_questions=[],
        proposed_modules=specification.modules,
        proposed_verification_objectives=specification.verification_objectives,
        summary="A reviewed structured specification for the data path.",
    )


class FakePlanner:
    def __init__(self, attempts=None):
        self.calls = 0
        self.contexts = []
        self.attempts = list(attempts or [planner_proposal()])

    async def propose(self, context, correction=None):
        self.calls += 1
        self.contexts.append((context, correction))
        attempt = self.attempts[min(self.calls - 1, len(self.attempts) - 1)]
        if isinstance(attempt, Exception):
            raise attempt
        return attempt


def wait_for_state(client, project_id, planner_id, expected):
    for _ in range(100):
        response = client.get(f"/api/projects/{project_id}/specification/planner/{planner_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] == expected:
            return body
        if body["state"] in {"failed", "cancelled", "interrupted"} and body["state"] != expected:
            raise AssertionError(body)
        time.sleep(0.02)
    raise AssertionError(f"planner did not reach {expected}")


def test_disabled_planner_never_calls_provider(app_settings):
    fake = FakePlanner()
    app = create_app(app_settings, planner_provider=fake)
    with TestClient(app) as client:
        project = create_project(client, requirements="Describe a data path.")
        response = client.post(
            f"/api/projects/{project['id']}/specification/planner",
            json={"expected_revision": 1, "instruction": "Draft the structured specification."},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "specification_planner_disabled"
        assert fake.calls == 0


class FakeModelClient:
    supports_json_mode = True

    def __init__(self, content):
        self.content = content
        self.calls = []

    async def complete(self, messages, tools, max_completion_tokens, response_format=None):
        self.calls.append((messages, tools, max_completion_tokens, response_format))
        return ProviderResponse(
            content=self.content,
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            request_id="planner-test-request",
        )


def test_meta_planner_adapter_parses_json_without_external_requests(app_settings):
    fake_client = FakeModelClient(planner_proposal().model_dump_json())
    settings = replace(app_settings, model_api_key="test-only-key")
    adapter = MetaRequirementsPlanner(fake_client, settings)
    project = type("Project", (), {"name": "Planner test", "description": "offline", "requirements": "Build a small data path."})()
    revision = type("Revision", (), {"data_json": CircuitSpecification.model_validate({**base_spec(), "original_request": "Build a small data path."}).model_dump(mode="json")})()
    context = build_planner_context(project, revision, ValidationReport(valid=False), "Draft the specification", 96_000)
    proposal = asyncio.run(adapter.propose(context))
    assert proposal.proposed_specification.circuit_name == "planned_data_path"
    assert adapter.last_usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert fake_client.calls[0][1] == []
    assert fake_client.calls[0][3] == {"type": "json_object"}


def test_planner_adapter_accepts_bare_typed_specification_from_provider(app_settings):
    specification = CircuitSpecification.model_validate({**base_spec(), "original_request": "Build a small data path."})
    response = ProviderResponse(
        content=specification.model_dump_json(),
        tool_calls=[],
        finish_reason="stop",
        usage=ProviderUsage(),
        request_id="planner-bare-spec",
    )
    project = type("Project", (), {"name": "Planner test", "description": "offline", "requirements": "Build a small data path."})()
    revision = type("Revision", (), {"data_json": specification.model_dump(mode="json")})()
    context = build_planner_context(project, revision, ValidationReport(valid=False), "Draft the specification", 96_000)

    proposal = parse_planner_response(response, context)

    assert proposal.proposed_specification.original_request == context.original_request
    assert proposal.proposed_modules == specification.modules


def test_planner_parser_accepts_reasoning_and_markdown_wrappers():
    specification = CircuitSpecification.model_validate({**base_spec(), "original_request": "Build a small data path."})
    response = ProviderResponse(
        content=f"<think>Planning is complete.</think>\n```json\n{specification.model_dump_json()}\n```",
        tool_calls=[],
        finish_reason="stop",
        usage=ProviderUsage(),
        request_id="planner-wrapped-spec",
    )
    project = type("Project", (), {"name": "Planner test", "description": "offline", "requirements": "Build a small data path."})()
    revision = type("Revision", (), {"data_json": specification.model_dump(mode="json")})()
    context = build_planner_context(project, revision, ValidationReport(valid=False), "Draft the specification", 96_000)

    proposal = parse_planner_response(response, context)

    assert proposal.proposed_specification.original_request == context.original_request


def test_planner_proposal_is_reviewable_accepted_and_persistent(app_settings):
    fake = FakePlanner()
    settings = replace(app_settings, specification_planner_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, planner_provider=fake)
    with TestClient(app) as client:
        project = create_project(client, requirements="Build a small data path.")
        initial = client.get(f"/api/projects/{project['id']}/specification").json()
        response = client.post(
            f"/api/projects/{project['id']}/specification/planner",
            json={"expected_revision": initial["current_revision"], "instruction": "Turn the request into a complete structured specification."},
        )
        assert response.status_code == 202, response.text
        run = wait_for_state(client, project["id"], response.json()["id"], "proposed")
        assert fake.calls == 1
        assert fake.contexts[0][0].original_request == "Build a small data path."
        assert fake.contexts[0][0].project_context["project_name"] == project["name"]
        assert run["proposal"]["proposed_specification"]["circuit_name"] == "planned_data_path"
        assert client.get(f"/api/projects/{project['id']}/specification").json()["current_revision"] == initial["current_revision"]

        accepted = client.post(
            f"/api/projects/{project['id']}/specification/planner/{run['id']}/accept",
            json={"expected_revision": initial["current_revision"]},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "accepted"
        assert accepted.json()["accepted_revision"] == initial["current_revision"] + 1
        assert client.get(f"/api/projects/{project['id']}/specification").json()["specification"]["circuit_name"] == "planned_data_path"
        planner_id = run["id"]

    restarted = create_app(settings, planner_provider=FakePlanner())
    with TestClient(restarted) as client:
        loaded = client.get(f"/api/projects/{project['id']}/specification/planner/{planner_id}")
        assert loaded.status_code == 200
        assert loaded.json()["state"] == "accepted"
        assert loaded.json()["proposal"]["summary"] == "A reviewed structured specification for the data path."


def test_planner_correction_is_bounded_and_retries_with_reason(app_settings):
    fake = FakePlanner([PlannerContractError("invalid JSON"), planner_proposal()])
    settings = replace(app_settings, specification_planner_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, planner_provider=fake)
    with TestClient(app) as client:
        project = create_project(client, requirements="Build a small data path.")
        response = client.post(f"/api/projects/{project['id']}/specification/planner", json={"expected_revision": 1})
        run = wait_for_state(client, project["id"], response.json()["id"], "proposed")
        assert run["correction_attempts"] == 1
        assert fake.calls == 2
        assert fake.contexts[1][1] == "invalid JSON"


def test_planner_acceptance_rejects_stale_revision_and_cross_project_access(app_settings):
    fake = FakePlanner()
    settings = replace(app_settings, specification_planner_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, planner_provider=fake)
    with TestClient(app) as client:
        first = create_project(client, name="First", requirements="Build a small data path.")
        second = create_project(client, name="Second", requirements="Build another data path.")
        response = client.post(f"/api/projects/{first['id']}/specification/planner", json={"expected_revision": 1})
        run = wait_for_state(client, first["id"], response.json()["id"], "proposed")
        saved = save_spec(client, first["id"], base_spec(), expected_revision=1)
        assert saved["current_revision"] == 2
        conflict = client.post(
            f"/api/projects/{first['id']}/specification/planner/{run['id']}/accept",
            json={"expected_revision": 1},
        )
        assert conflict.status_code == 409
        assert client.get(f"/api/projects/{second['id']}/specification/planner/{run['id']}").status_code == 404


class BlockingPlanner:
    def __init__(self):
        self.calls = 0

    async def propose(self, context, correction=None):
        self.calls += 1
        await asyncio.sleep(30)
        return planner_proposal()


def test_planner_cancellation_is_persisted(app_settings):
    fake = BlockingPlanner()
    settings = replace(app_settings, specification_planner_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, planner_provider=fake)
    with TestClient(app) as client:
        project = create_project(client, requirements="Build a small data path.")
        response = client.post(f"/api/projects/{project['id']}/specification/planner", json={"expected_revision": 1})
        planner_id = response.json()["id"]
        for _ in range(100):
            state = client.get(f"/api/projects/{project['id']}/specification/planner/{planner_id}").json()["state"]
            if state == "running":
                break
            time.sleep(0.01)
        cancelled = client.post(f"/api/projects/{project['id']}/specification/planner/{planner_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert fake.calls == 1
