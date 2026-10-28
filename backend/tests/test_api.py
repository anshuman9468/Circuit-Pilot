from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import create_project


def test_health_and_project_crud(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "circuitpilot-backend"}
    project = create_project(client)
    project_id = project["id"]
    assert client.get("/api/projects").json()[0]["id"] == project_id
    renamed = client.patch(f"/api/projects/{project_id}", json={"name": "Counter Core v2"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Counter Core v2"
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_requirements_and_file_lifecycle(client):
    project = create_project(client)
    project_id = project["id"]
    saved = client.put(
        f"/api/projects/{project_id}/requirements",
        json={"content": "Use a parameterized width and an active-low reset."},
    )
    assert saved.status_code == 200
    assert client.get(f"/api/projects/{project_id}/requirements").json()["content"].startswith("Use a parameterized")

    created = client.post(
        f"/api/projects/{project_id}/files",
        json={"path": "rtl/counter.sv", "content": "module counter;\nendmodule\n"},
    )
    assert created.status_code == 201, created.text
    file_id = created.json()["id"]
    assert client.post(
        f"/api/projects/{project_id}/files",
        json={"path": "rtl/counter.sv", "content": "duplicate"},
    ).status_code == 409
    updated = client.patch(
        f"/api/projects/{project_id}/files/{file_id}",
        json={"content": "module counter #(parameter WIDTH = 8);\nendmodule\n"},
    )
    assert updated.status_code == 200
    renamed = client.patch(
        f"/api/projects/{project_id}/files/{file_id}",
        json={"path": "rtl/pulse_counter.sv"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["path"] == "rtl/pulse_counter.sv"
    assert client.get(f"/api/projects/{project_id}/files/{file_id}").json()["content"].startswith("module counter")
    assert client.delete(f"/api/projects/{project_id}/files/{file_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}/files/{file_id}").status_code == 404


def test_persistence_after_new_app_instance(app_settings):
    first = create_app(app_settings)
    with TestClient(first) as client:
        project = create_project(client, requirements="Persist me")
        project_id = project["id"]
        client.post(f"/api/projects/{project_id}/files", json={"path": "notes.md", "content": "saved file"})

    second = create_app(app_settings)
    with TestClient(second) as client:
        loaded = client.get(f"/api/projects/{project_id}")
        assert loaded.status_code == 200
        assert loaded.json()["requirements"] == "Persist me"
        files = client.get(f"/api/projects/{project_id}/files").json()
        assert [file["path"] for file in files] == ["notes.md"]
        assert client.get(f"/api/projects/{project_id}/activity").json()


def test_project_isolation_and_safe_paths(client):
    first = create_project(client, "First")
    second = create_project(client, "Second")
    first_id, second_id = first["id"], second["id"]
    created = client.post(f"/api/projects/{first_id}/files", json={"path": "main.v", "content": "module main; endmodule"})
    file_id = created.json()["id"]

    assert client.get(f"/api/projects/{second_id}/files/{file_id}").status_code == 404
    assert client.post(f"/api/projects/{first_id}/files", json={"path": "../escape.v", "content": "bad"}).status_code == 400
    assert client.post(f"/api/projects/{first_id}/files", json={"path": "/tmp/escape.v", "content": "bad"}).status_code == 400
    assert client.post(f"/api/projects/{first_id}/files", json={"path": "rtl//main.v", "content": "bad"}).status_code == 400
    assert client.post(f"/api/projects/{first_id}/files", json={"path": "main.py", "content": "bad"}).status_code == 400


def test_rejects_oversized_content_and_missing_projects(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/projects/{missing}").status_code == 404
    project = create_project(client)
    project_id = project["id"]
    oversized = client.post(
        f"/api/projects/{project_id}/files",
        json={"path": "large.txt", "content": "x" * 257},
    )
    assert oversized.status_code in {413, 422}
    requirements = client.put(
        f"/api/projects/{project_id}/requirements", json={"content": "x" * 301}
    )
    assert requirements.status_code in {413, 422}


def test_symlink_escape_is_rejected(client, app_settings):
    project = create_project(client)
    project_dir = app_settings.workspace_root / project["id"]
    outside = app_settings.workspace_root / "outside"
    outside.mkdir(parents=True)
    try:
        os.symlink(outside, project_dir / "linked")
    except OSError:
        # Symlinks are available in the supported local development environments;
        # retain a useful pass on platforms where the OS disallows them.
        return
    response = client.post(
        f"/api/projects/{project['id']}/files",
        json={"path": "linked/escape.txt", "content": "must not write"},
    )
    assert response.status_code == 400
    assert not (outside / "escape.txt").exists()
