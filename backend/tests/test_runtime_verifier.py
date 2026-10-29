"""No DB or network: reject the wrong API before signup or other business writes."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import make_url


@pytest.fixture
def verifier_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify-runtime.py"
    spec = importlib.util.spec_from_file_location("runtime_verifier_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("wrong_api", ["unknown_account", "different_identity"])
def test_wrong_api_never_receives_signup_or_other_business_writes(
    verifier_module, monkeypatch, wrong_api
):
    marker_id = "31aa9364-d6d0-4cc4-88e9-75727c489682"
    marker_email = "runtime-marker@example.com"
    marker_cleaned = []

    @contextmanager
    def fake_identity(_database):
        try:
            yield marker_id, marker_email, "synthetic-marker-password"
        finally:
            marker_cleaned.append(True)

    monkeypatch.setattr(verifier_module, "validation_identity", fake_identity)
    requests = []

    def wrong_server(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/login/access-token":
            if wrong_api == "unknown_account":
                return httpx.Response(400, json={"detail": "unrecognized identity"})
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        if request.url.path == "/api/v1/users/me":
            return httpx.Response(200, json={"id": "wrong-id", "email": marker_email})
        pytest.fail(
            "Wrong API received a business request before identity verification"
        )

    report = {"checks": [], "synthetic_accounts": []}
    verifier = verifier_module.RuntimeVerifier("http://127.0.0.1:18123/api/v1", report)
    verifier.client.close()
    verifier.client = httpx.Client(
        base_url="http://127.0.0.1:18123/api/v1/",
        transport=httpx.MockTransport(wrong_server),
    )
    try:
        with pytest.raises(verifier_module.VerificationFailure):
            verifier.run(
                make_url(
                    "postgresql+psycopg://localhost:15432/devassist_validation_fake"
                )
            )
    finally:
        verifier.client.close()
    assert requests == (
        [("POST", "/api/v1/login/access-token")]
        if wrong_api == "unknown_account"
        else [("POST", "/api/v1/login/access-token"), ("GET", "/api/v1/users/me")]
    )
    assert marker_cleaned == [True]
    assert report["synthetic_accounts"] == []
    assert not report.get("api_database_identity_verified")


def test_matching_api_identity_is_required_before_signup(verifier_module, monkeypatch):
    marker_id = "31aa9364-d6d0-4cc4-88e9-75727c489682"
    marker_email = "runtime-marker@example.com"

    @contextmanager
    def fake_identity(_database):
        yield marker_id, marker_email, "synthetic-marker-password"

    monkeypatch.setattr(verifier_module, "validation_identity", fake_identity)
    requests = []

    def matching_server(request):
        requests.append(request.url.path)
        if request.url.path == "/api/v1/login/access-token":
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        assert request.url.path == "/api/v1/users/me"
        return httpx.Response(200, json={"id": marker_id, "email": marker_email})

    report = {"checks": [], "synthetic_accounts": []}
    verifier = verifier_module.RuntimeVerifier("http://127.0.0.1:18123/api/v1", report)
    verifier.client.close()
    verifier.client = httpx.Client(
        base_url="http://127.0.0.1:18123/api/v1/",
        transport=httpx.MockTransport(matching_server),
    )

    def first_business_action(_suffix):
        assert report["api_database_identity_verified"] is True
        assert requests == ["/api/v1/login/access-token", "/api/v1/users/me"]
        raise StopIteration("Stop before business I/O")

    monkeypatch.setattr(verifier, "account", first_business_action)
    try:
        with pytest.raises(StopIteration):
            verifier.run(
                make_url(
                    "postgresql+psycopg://localhost:15432/devassist_validation_fake"
                )
            )
    finally:
        verifier.client.close()


def test_workspace_verifier_checks_real_route_conflicts_without_database(
    verifier_module, monkeypatch, tmp_path
):
    import uuid
    from types import SimpleNamespace

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.routes.code import router
    from app.core.config import settings

    first_owner, second_owner = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "CODE_WORKSPACE_DIR", tmp_path / "workspace")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def identity(request: Request):
        owner = first_owner if request.headers.get("Authorization") == "Bearer first" else second_owner
        return SimpleNamespace(id=owner)

    app.dependency_overrides[get_current_user] = identity

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "offline-test"
        return await call_next(request)

    report = {"checks": [], "synthetic_accounts": []}
    verifier = verifier_module.RuntimeVerifier("http://testserver/api/v1", report)
    verifier.client.close()
    with TestClient(app, base_url="http://testserver/api/v1/") as client:
        verifier.client = client
        verifier.verify_workspace("first", "second", "synthetic")
    assert all(check["passed"] for check in report["checks"])
    assert {check["name"] for check in report["checks"]} >= {
        "workspace_manual_conflict",
        "workspace_apply_conflict",
        "workspace_conflict_other_read",
        "workspace_apply",
    }
