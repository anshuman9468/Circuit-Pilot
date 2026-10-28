from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app_settings(tmp_path):
    return Settings(
        database_path=tmp_path / "circuitpilot.db",
        workspace_root=tmp_path / "workspaces",
        allowed_origins=("http://testserver",),
        max_file_size=256,
        # Specifications are structured JSON documents and are intentionally
        # larger than the small file/requirements limits exercised below.
        max_request_size=32 * 1024,
        max_requirements_size=300,
    )


@pytest.fixture
def client(app_settings):
    app = create_app(app_settings)
    with TestClient(app) as test_client:
        yield test_client


def create_project(client, name="Counter Core", requirements="Implement a synchronous counter."):
    response = client.post(
        "/api/projects",
        json={"name": name, "description": "A test project", "requirements": requirements},
    )
    assert response.status_code == 201, response.text
    return response.json()
