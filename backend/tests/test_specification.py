from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import create_project


def base_spec(category="combinational"):
    return {
        "original_request": "Build a small data path.",
        "circuit_name": "data_path",
        "purpose": "Move a bounded value from input to output.",
        "design_category": category,
        "parameters": [{"name": "WIDTH", "default_value": 8}],
        "ports": [
            {"name": "data_in", "direction": "input", "width": "WIDTH", "signedness": "unsigned", "purpose": "Input value"},
            {"name": "data_out", "direction": "output", "width": "WIDTH", "signedness": "unsigned", "purpose": "Output value"},
        ],
        "functional_requirements": [{"id": "req-transfer", "title": "Transfer", "statement": "The output reflects the input value."}],
        "modules": [{"name": "data_path", "responsibility": "Top-level data path", "input_ports": ["data_in"], "output_ports": ["data_out"], "requirement_ids": ["req-transfer"], "is_top_level": True}],
        "verification_objectives": [{"id": "verify-transfer", "title": "Transfer check", "requirement_ids": ["req-transfer"], "stimulus": "Drive representative values.", "expected_behavior": "Output follows the specified transfer behavior.", "check_type": "directed"}],
    }


def save_spec(client, project_id, spec, expected_revision=1):
    response = client.put(
        f"/api/projects/{project_id}/specification",
        json={"expected_revision": expected_revision, "specification": spec},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_valid_combinational_spec_does_not_require_clock(client):
    project = create_project(client, requirements="Build a small data path.")
    response = save_spec(client, project["id"], base_spec())
    assert response["validation"]["valid"] is True
    assert response["specification"]["clock_domains"] == []
    assert response["specification"]["original_request"] == "Build a small data path."


def test_sequential_spec_with_explicit_clock_and_reset_is_structurally_valid(client):
    project = create_project(client)
    spec = base_spec("sequential")
    spec["ports"] += [
        {"name": "clk", "direction": "input", "width": 1, "signedness": "unsigned"},
        {"name": "rst", "direction": "input", "width": 1, "signedness": "unsigned"},
    ]
    spec["clock_domains"] = [{"name": "core_clk", "signal": "clk", "edge": "rising"}]
    spec["reset_intent"] = "required"
    spec["reset_signals"] = [{"name": "core_reset", "signal": "rst", "polarity": "active_high", "behavior": "synchronous", "associated_clock_domain": "core_clk", "intended_reset_state": 0}]
    response = save_spec(client, project["id"], spec)
    assert response["validation"]["valid"] is True
    assert response["validation"]["blocking_count"] == 0


def test_sequential_spec_can_explicitly_have_no_reset(client):
    project = create_project(client)
    spec = base_spec("sequential")
    spec["ports"].append({"name": "clk", "direction": "input", "width": 1, "signedness": "unsigned"})
    spec["clock_domains"] = [{"name": "core_clk", "signal": "clk", "edge": "rising"}]
    spec["reset_intent"] = "not_intended"
    response = save_spec(client, project["id"], spec)
    assert response["validation"]["valid"] is True
    assert response["specification"]["reset_signals"] == []


def test_invalid_widths_and_references_are_rejected_or_reported(client):
    project = create_project(client)
    invalid_payload = base_spec()
    invalid_payload["ports"][0]["width"] = 0
    response = client.put(f"/api/projects/{project['id']}/specification", json={"expected_revision": 1, "specification": invalid_payload})
    assert response.status_code == 422

    unknown = base_spec()
    unknown["ports"][0]["width"] = "MISSING_WIDTH"
    unknown["modules"][0]["input_ports"] = ["missing_port"]
    unknown["verification_objectives"][0]["requirement_ids"] = ["missing_requirement"]
    response = save_spec(client, project["id"], unknown)
    codes = {item["code"] for item in response["validation"]["findings"] if item["status"] == "open"}
    assert {"unknown_width_parameter", "module_port_unknown", "verification_requirement_unknown"} <= codes


def test_duplicate_names_width_mismatch_and_symbolic_limitations_are_reported(client):
    project = create_project(client)
    spec = base_spec()
    spec["ports"].append({"name": "data_in", "direction": "input", "width": 16, "signedness": "unsigned"})
    spec["modules"][0]["connections"] = [{"from_module": "data_path", "from_port": "data_out", "to_module": "data_path", "to_port": "data_in"}]
    response = save_spec(client, project["id"], spec)
    codes = {item["code"] for item in response["validation"]["findings"] if item["status"] == "open"}
    assert "duplicate_port" in codes
    assert "connection_width_mismatch" in codes

    symbolic = base_spec()
    symbolic["parameters"][0]["default_value"] = None
    symbolic["modules"][0]["connections"] = [{"from_module": "data_path", "from_port": "data_in", "to_module": "data_path", "to_port": "data_out"}]
    response = save_spec(client, project["id"], symbolic, expected_revision=response["current_revision"])
    codes = {item["code"] for item in response["validation"]["findings"] if item["status"] == "open"}
    assert "connection_symbolic_width_unresolved" in codes


def test_clarification_answer_creates_revision_and_resolves_underlying_issue(client):
    project = create_project(client)
    spec = base_spec()
    spec["ports"][0].pop("direction")
    response = save_spec(client, project["id"], spec)
    clarification = next(item for item in response["validation"]["findings"] if item["code"] == "port_direction_missing" and item["status"] == "open")
    answered = client.patch(
        f"/api/projects/{project['id']}/specification/clarifications/{clarification['id']}",
        json={"expected_revision": response["current_revision"], "answer": "input"},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["current_revision"] == response["current_revision"] + 1
    resolved = next(item for item in answered.json()["validation"]["findings"] if item["id"] == clarification["id"])
    assert resolved["status"] == "resolved"
    assert answered.json()["specification"]["ports"][0]["direction"] == "input"


def test_readiness_requires_validity_and_ready_revision_is_preserved(client):
    project = create_project(client)
    incomplete = client.post(f"/api/projects/{project['id']}/specification/ready", json={"expected_revision": 1})
    assert incomplete.status_code == 422
    draft = save_spec(client, project["id"], base_spec())
    ready = client.post(f"/api/projects/{project['id']}/specification/ready", json={"expected_revision": draft["current_revision"]})
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"
    ready_revision = ready.json()["current_revision"]

    edited = base_spec()
    edited["purpose"] = "A revised purpose after review."
    next_draft = save_spec(client, project["id"], edited, expected_revision=ready_revision)
    assert next_draft["status"] == "draft"
    history = client.get(f"/api/projects/{project['id']}/specification/revisions").json()
    assert any(item["revision"] == ready_revision and item["status"] == "ready" for item in history)
    assert history[0]["revision"] == next_draft["current_revision"]


def test_conflicting_save_and_cross_project_access_are_rejected(app_settings):
    app = create_app(app_settings)
    with TestClient(app) as client:
        first = create_project(client, name="First")
        second = create_project(client, name="Second")
        initial = client.get(f"/api/projects/{first['id']}/specification").json()
        saved = save_spec(client, first["id"], base_spec(), initial["current_revision"])
        conflict = client.put(f"/api/projects/{first['id']}/specification", json={"expected_revision": initial["current_revision"], "specification": base_spec()})
        assert conflict.status_code == 409
        assert client.get(f"/api/projects/{second['id']}/specification/revisions/1").status_code == 404
        exported = client.get(f"/api/projects/{first['id']}/specification/export")
        assert exported.status_code == 200
        assert exported.json()["specification"]["revision"] == saved["current_revision"]


def test_revision_history_and_project_deletion_are_persistent(app_settings):
    app = create_app(app_settings)
    with TestClient(app) as client:
        project = create_project(client)
        project_id = project["id"]
        initial = client.get(f"/api/projects/{project_id}/specification").json()
        save_spec(client, project_id, base_spec(), initial["current_revision"])
    restarted = create_app(app_settings)
    with TestClient(restarted) as client:
        history = client.get(f"/api/projects/{project_id}/specification/revisions").json()
        assert len(history) == 2
        assert client.get(f"/api/projects/{project_id}/specification").json()["specification"]["circuit_name"] == "data_path"
        assert client.delete(f"/api/projects/{project_id}").status_code == 204
        assert client.get(f"/api/projects/{project_id}/specification").status_code == 404
