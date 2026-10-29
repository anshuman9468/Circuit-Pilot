from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from app.generation import GenerationClarification, GenerationResult, validate_output_limits
from app.errors import ApiError
from app.main import create_app
from app.services.generation_service import GenerationAttempt, GenerationContractError

from conftest import create_project
from test_specification import base_spec, save_spec


def ready_project(client):
    project = create_project(client, requirements="Build a parameterized data path.")
    initial = client.get(f"/api/projects/{project['id']}/specification").json()
    saved = save_spec(client, project["id"], base_spec(), initial["current_revision"])
    ready = client.post(
        f"/api/projects/{project['id']}/specification/ready",
        json={"expected_revision": saved["current_revision"]},
    )
    assert ready.status_code == 200, ready.text
    return project, ready.json()["current_revision"]


def valid_result() -> GenerationResult:
    return GenerationResult.model_validate(
        {
            "top_module": "data_path",
            "testbench_top": "data_path_tb",
            "files": [
                {"path": "rtl/data_path.sv", "role": "rtl", "language": "systemverilog", "content": "module data_path; endmodule\n"},
                {"path": "tb/data_path_tb.sv", "role": "testbench", "language": "systemverilog", "content": "module data_path_tb; initial $finish; endmodule\n"},
            ],
            "source_manifest": [
                {"path": "rtl/data_path.sv", "role": "rtl", "order": 0},
                {"path": "tb/data_path_tb.sv", "role": "testbench", "order": 1},
            ],
            "requirement_module_mapping": {"req-transfer": ["data_path"]},
            "verification_test_mapping": {"verify-transfer": ["data_path_tb"]},
            "assumptions": [],
            "unresolved_questions": [],
            "known_limitations": ["Not compiled or simulated in Phase 4."],
            "implementation_summary": "A staged data path RTL and self-checking testbench proposal.",
        }
    )


class FakeGenerationProvider:
    def __init__(self, attempts=None):
        self.calls = 0
        self.contexts = []
        self.attempts = list(attempts or [GenerationAttempt(valid_result(), [], "fake")])

    async def generate(self, context, correction=None):
        self.calls += 1
        self.contexts.append((context, correction))
        attempt = self.attempts[min(self.calls - 1, len(self.attempts) - 1)]
        if isinstance(attempt, Exception):
            raise attempt
        return attempt


def wait_for_state(client, project_id, generation_id, expected):
    for _ in range(100):
        response = client.get(f"/api/projects/{project_id}/generations/{generation_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] == expected:
            return body
        if body["state"] in {"failed", "cancelled", "interrupted", "awaiting_clarification"} and body["state"] != expected:
            raise AssertionError(body)
        time.sleep(0.02)
    raise AssertionError(f"generation did not reach {expected}")


def test_disabled_generation_never_calls_provider(app_settings):
    fake = FakeGenerationProvider()
    app = create_app(app_settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"specification_revision": revision},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "generation_disabled"
        assert fake.calls == 0


def test_generation_contract_rejects_paths_duplicates_missing_manifest_and_oversized_content():
    data = valid_result().model_dump()
    data["files"][0]["path"] = "../escape.sv"
    with pytest.raises(ValueError):
        GenerationResult.model_validate(data)
    data = valid_result().model_dump()
    data["files"].append(data["files"][0].copy())
    with pytest.raises(ValueError):
        GenerationResult.model_validate(data)
    data = valid_result().model_dump()
    data["source_manifest"] = data["source_manifest"][:1]
    with pytest.raises(ValueError):
        GenerationResult.model_validate(data)
    with pytest.raises(ApiError):
        validate_output_limits(valid_result(), 10)


def test_generation_stages_and_applies_without_touching_active_files(app_settings):
    fake = FakeGenerationProvider()
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        response = client.post(f"/api/projects/{project['id']}/generations", json={"specification_revision": revision})
        assert response.status_code == 202, response.text
        job = wait_for_state(client, project["id"], response.json()["id"], "proposed")
        assert fake.calls == 1
        assert fake.contexts[0][0].specification.revision == revision
        assert client.get(f"/api/projects/{project['id']}/files").json() == []
        proposal = client.get(f"/api/projects/{project['id']}/generation-proposals/{job['proposal_id']}")
        assert proposal.status_code == 200
        assert {item["change_type"] for item in proposal.json()["files"]} == {"added"}
        applied = client.post(f"/api/projects/{project['id']}/generation-proposals/{job['proposal_id']}/accept")
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "applied"
        files = client.get(f"/api/projects/{project['id']}/files").json()
        assert {item["path"] for item in files} == {"rtl/data_path.sv", "tb/data_path_tb.sv"}
        assert client.get(f"/api/projects/{project['id']}/generations/{job['id']}").json()["state"] == "applied"


def test_generation_correction_is_bounded_and_preserves_spec_revision(app_settings):
    fake = FakeGenerationProvider([GenerationContractError("bad JSON"), GenerationAttempt(valid_result(), [], "fixed")])
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        response = client.post(f"/api/projects/{project['id']}/generations", json={"specification_revision": revision})
        job = wait_for_state(client, project["id"], response.json()["id"], "proposed")
        assert fake.calls == 2
        assert fake.contexts[1][1] == "bad JSON"
        assert job["specification_revision"] == revision


def test_generation_returns_clarification_instead_of_inventing_missing_data(app_settings):
    clarification = [{"id": "missing-reset", "related_field": "reset", "reason": "Reset intent is unresolved.", "severity": "blocking", "expected_answer_type": "choice"}]
    fake = FakeGenerationProvider([GenerationAttempt(None, [GenerationClarification.model_validate(item) for item in clarification], "questions")])
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        response = client.post(f"/api/projects/{project['id']}/generations", json={"specification_revision": revision})
        body = wait_for_state(client, project["id"], response.json()["id"], "awaiting_clarification")
        assert body["clarification"][0]["id"] == "missing-reset"
        assert body["specification_revision"] == revision


def test_generation_rejects_cross_project_context_and_workspace_conflicts(app_settings):
    fake = FakeGenerationProvider()
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        first, revision = ready_project(client)
        second, second_revision = ready_project(client)
        created = client.post(f"/api/projects/{second['id']}/files", json={"path": "rtl/existing.sv", "content": "module existing; endmodule"}).json()
        response = client.post(
            f"/api/projects/{first['id']}/generations",
            json={"specification_revision": revision, "settings": {"include_existing_context": True, "context_file_ids": [created["id"]]}},
        )
        assert response.status_code == 400
        assert fake.calls == 0

        response = client.post(f"/api/projects/{first['id']}/generations", json={"specification_revision": revision})
        job = wait_for_state(client, first["id"], response.json()["id"], "proposed")
        added = client.post(f"/api/projects/{first['id']}/files", json={"path": "rtl/user.sv", "content": "module user; endmodule"})
        assert added.status_code == 201
        conflict = client.post(f"/api/projects/{first['id']}/generation-proposals/{job['proposal_id']}/accept")
        assert conflict.status_code == 409
        assert second_revision >= 1


def test_generation_proposal_and_overwrite_backup_survive_backend_restart(app_settings):
    fake = FakeGenerationProvider()
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        existing = client.post(
            f"/api/projects/{project['id']}/files",
            json={"path": "rtl/data_path.sv", "content": "module old_data_path; endmodule"},
        )
        assert existing.status_code == 201
        response = client.post(f"/api/projects/{project['id']}/generations", json={"specification_revision": revision})
        job = wait_for_state(client, project["id"], response.json()["id"], "proposed")
        assert client.get(f"/api/projects/{project['id']}/files/{existing.json()['id']}").json()["content"] == "module old_data_path; endmodule"
        accepted = client.post(f"/api/projects/{project['id']}/generation-proposals/{job['proposal_id']}/accept")
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["backup_count"] == 2
        project_id = project["id"]
        proposal_id = job["proposal_id"]

    restarted = create_app(settings, generation_provider=FakeGenerationProvider())
    with TestClient(restarted) as client:
        loaded = client.get(f"/api/projects/{project_id}/generation-proposals/{proposal_id}")
        assert loaded.status_code == 200
        assert loaded.json()["status"] == "applied"
        assert "module data_path" in client.get(f"/api/projects/{project_id}/files/{existing.json()['id']}").json()["content"]


class BlockingGenerationProvider:
    def __init__(self):
        self.calls = 0

    async def generate(self, context, correction=None):
        self.calls += 1
        await asyncio.sleep(30)
        return GenerationAttempt(valid_result(), [], "never")


def test_generation_cancellation_is_persisted(app_settings):
    fake = BlockingGenerationProvider()
    settings = replace(app_settings, generation_enabled=True, model_api_key="test-only-key")
    app = create_app(settings, generation_provider=fake)
    with TestClient(app) as client:
        project, revision = ready_project(client)
        response = client.post(f"/api/projects/{project['id']}/generations", json={"specification_revision": revision})
        generation_id = response.json()["id"]
        for _ in range(100):
            state = client.get(f"/api/projects/{project['id']}/generations/{generation_id}").json()["state"]
            if state == "running":
                break
            time.sleep(0.01)
        cancelled = client.post(f"/api/projects/{project['id']}/generations/{generation_id}/cancel")
        assert cancelled.status_code == 200
        for _ in range(100):
            state = client.get(f"/api/projects/{project['id']}/generations/{generation_id}").json()["state"]
            if state == "cancelled":
                break
            time.sleep(0.01)
        assert state == "cancelled"
        assert fake.calls == 1
