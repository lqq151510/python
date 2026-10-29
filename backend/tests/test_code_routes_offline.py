"""Code workspace HTTP contract using temporary files and a stubbed identity.

Run with pytest --noconftest; this deliberately skips the database fixture and
never exercises real login, models, or the application's configured workspace.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.routes.code import router
from app.core.config import settings


@pytest.fixture
def workspace_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "CODE_WORKSPACE_DIR", tmp_path / "workspace")
    owner = uuid.uuid4()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "test"
        return await call_next(request)

    with TestClient(app) as client:
        yield client


def test_diff_apply_and_conflict_are_consistent(workspace_client: TestClient) -> None:
    client = workspace_client
    assert client.put("/code/workspace/files", json={"path": "a.py", "content": "original", "base_version": "missing"}).status_code == 200
    preview = client.post("/code/workspace/diff", json={"path": "a.py", "content": "new"})
    assert preview.status_code == 200
    version = preview.json()["base_version"]
    assert version.startswith("sha256:") and len(version) == 71
    missing = client.post("/code/workspace/diff", json={"path": "b.py", "content": "new"})
    assert missing.status_code == 200
    assert missing.json()["base_version"] == "missing"

    body = {
        "permission": "workspace_modify",
        "approved": True,
        "edits": [{"path": "a.py", "content": "new"}],
    }
    assert client.post("/code/workspace/apply", json=body).status_code == 422
    assert client.put("/code/workspace/files", json={"path": "a.py", "content": "other", "base_version": version}).status_code == 200
    body["edits"] = [
        {"path": "a.py", "content": "new", "base_version": version},
        {"path": "b.py", "content": "new", "base_version": "missing"},
    ]
    conflict = client.post("/code/workspace/apply", json=body)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "workspace_conflict"
    assert client.get("/code/workspace/files/a.py").json()["content"] == "other"
    assert client.get("/code/workspace/files/b.py").status_code == 404


def test_apply_accepts_fresh_file_versions(workspace_client: TestClient) -> None:
    client = workspace_client
    assert client.put("/code/workspace/files", json={"path": "a.py", "content": "before", "base_version": "missing"}).status_code == 200
    preview = client.post("/code/workspace/diff", json={"path": "a.py", "content": "after"})
    assert preview.status_code == 200
    response = client.post("/code/workspace/apply", json={
        "permission": "workspace_modify",
        "approved": True,
        "edits": [
            {"path": "a.py", "content": "after", "base_version": preview.json()["base_version"]},
            {"path": "b.py", "content": "created", "base_version": "missing"},
        ],
    })
    assert response.status_code == 200
    assert client.get("/code/workspace/files/a.py").json()["content"] == "after"
    assert client.get("/code/workspace/files/b.py").json()["content"] == "created"


def test_manual_save_requires_snapshot_and_rejects_stale_version(workspace_client: TestClient) -> None:
    client = workspace_client
    path = "/code/workspace/files"
    assert client.put(path, json={"path": "a.py", "content": "old"}).status_code == 422
    assert client.put(path, json={"path": "a.py", "content": "old", "base_version": "missing"}).status_code == 200
    snapshot = client.get(f"{path}/a.py").json()
    assert snapshot["content"] == "old"
    assert snapshot["base_version"].startswith("sha256:")
    assert client.put(path, json={"path": "a.py", "content": "other", "base_version": snapshot["base_version"]}).status_code == 200
    stale = client.put(path, json={"path": "a.py", "content": "draft", "base_version": snapshot["base_version"]})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "workspace_conflict"
    assert client.get(f"{path}/a.py").json()["content"] == "other"
