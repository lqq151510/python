"""Exercise monitoring with isolated ASGI routes and no database or provider."""

import importlib.util
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.observability import observe_operation, request_id_context
from app.core.request_id import APIRequestIDMiddleware


def application() -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIRequestIDMiddleware)

    @app.get("/api/v1/code/probe/{name}")
    def probe(request: Request, name: str) -> dict:
        del name
        return {
            "request_id": request.state.request_id,
            "context": request_id_context.get(),
        }

    @app.get("/api/v1/deny")
    def deny() -> None:
        raise HTTPException(401, "Not authenticated")

    @app.get("/api/v1/crash")
    def crash() -> None:
        raise RuntimeError("provider-secret-and-private-document")

    @app.get("/outside")
    def outside() -> dict:
        return {"ok": True}

    return app


def events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name in ("app.core.request_id", "app.core.observability")
    ]


def test_non_chat_api_correlation_and_private_path_redaction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        with TestClient(application()) as client:
            response = client.get(
                "/api/v1/code/probe/private-user-name?secret=private-query",
                headers={"Authorization": "Bearer private-token"},
            )
    request_id = response.headers["X-Request-ID"]
    assert UUID(request_id)
    assert response.json() == {"request_id": request_id, "context": request_id}
    event = events(caplog)[0]
    assert event["route"] == "/api/v1/code/probe/{name}"
    assert event["elapsed_ms"] >= 0
    assert event["status_code"] == 200
    assert request_id_context.get() is None
    for private in ("private-user-name", "private-query", "private-token"):
        assert private not in caplog.text


@pytest.mark.parametrize(
    "url,status", [("/api/v1/deny", 401), ("/api/v1/missing", 404)]
)
def test_http_errors_keep_correlation(
    url: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        with TestClient(application()) as client:
            response = client.get(url)
    assert response.status_code == status
    assert UUID(response.headers["X-Request-ID"])
    assert events(caplog)[0]["status_code"] == status


def test_unhandled_failure_is_safe_and_observable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        with TestClient(application()) as client:
            response = client.get(
                "/api/v1/crash", headers={"Origin": settings.FRONTEND_HOST}
            )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    assert response.headers["Access-Control-Allow-Origin"] == settings.FRONTEND_HOST
    event = events(caplog)[0]
    assert event["error_type"] == "RuntimeError"
    assert event["request_id"] == response.headers["X-Request-ID"]
    assert "provider-secret" not in caplog.text + response.text
    assert request_id_context.get() is None


def test_operation_failure_preserves_error_and_safe_terminal_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @observe_operation("vector_rank")
    def work() -> None:
        raise ValueError("private-query-and-token")

    token = request_id_context.set("test-request")
    try:
        with caplog.at_level(logging.INFO, logger="app"):
            with pytest.raises(ValueError):
                work()
    finally:
        request_id_context.reset(token)
    event = events(caplog)[0]
    assert event["state"] == "failed"
    assert event["operation"] == "vector_rank"
    assert event["request_id"] == "test-request"
    assert event["error_type"] == "ValueError"
    assert "private-query-and-token" not in caplog.text


def test_operation_success_preserves_return_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @observe_operation("vector_rank")
    def work() -> str:
        return "private-result"

    with caplog.at_level(logging.INFO, logger="app"):
        assert work() == "private-result"
    assert events(caplog)[0]["state"] == "succeeded"
    assert "private-result" not in caplog.text


def test_non_api_paths_are_outside_request_monitoring(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app"):
        with TestClient(application()) as client:
            response = client.get("/outside")
    assert response.status_code == 200
    assert "X-Request-ID" not in response.headers
    assert events(caplog) == []


def test_summary_counts_client_server_and_operation_failures() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "summarize-requests.py"
    spec = importlib.util.spec_from_file_location("request_summary", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        json.dumps({"event": "api_request", "status_code": status, "elapsed_ms": 10})
        for status in (200, 401, 500)
    ]
    rows += [
        "INFO:app:"
        + json.dumps(
            {
                "event": "operation_completed",
                "operation": "vector_rank",
                "state": "failed",
            }
        ),
        "irrelevant log",
    ]
    result = module.summarize(rows)
    assert result["requests"] == {"total": 3, "client_errors": 1, "server_errors": 1}
    assert result["operations"] == {"vector_rank:failed": 1}
    assert result["request_latency_ms"] == {"mean": 10.0, "max": 10}
    invalid = [
        json.dumps({"event": "api_request", "status_code": True, "elapsed_ms": 1}),
        json.dumps(
            {"event": "operation_completed", "operation": [], "state": "failed"}
        ),
        '{"event":broken-json}',
    ]
    assert module.summarize(invalid)["invalid_events"] == 3
