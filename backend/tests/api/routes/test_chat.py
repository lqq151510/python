import asyncio
import json
import logging
from collections.abc import Callable, Generator
from time import monotonic
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from app import chat
from app.api.deps import get_current_user
from app.core.config import Settings, settings
from app.main import app
from app.models import User

CHAT_URL = f"{settings.API_V1_STR}/chat"


@pytest.fixture
def authenticated_client(client: TestClient) -> Generator[TestClient]:
    app.dependency_overrides[get_current_user] = lambda: User(email="chat@example.com")
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def model_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SecretStr("test-only-secret"))
    monkeypatch.setattr(chat, "RETRY_DELAY_SECONDS", 0.01)


def completion_response(answer: str = "模拟回答") -> dict[str, Any]:
    return {
        "id": "mock-completion",
        "created": 1,
        "model": "deepseek-flash",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": answer},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }


def mock_provider(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], Any],
) -> dict[str, Any]:
    constructor_args: dict[str, Any] = {}

    def create_client(**kwargs: Any) -> AsyncOpenAI:
        constructor_args.update(kwargs)
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(chat, "AsyncOpenAI", create_client)
    return constructor_args


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert UUID(response.headers["X-Request-ID"])
    assert "test-only-secret" not in response.text


def test_success_payload_auth_and_sanitized_logs(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del model_config
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion_response("  provider-only-answer  "))

    client_args = mock_provider(monkeypatch, respond)
    with caplog.at_level(logging.INFO, logger="app"):
        response = authenticated_client.post(
            CHAT_URL,
            json={"message": "  user-private-question  "},
            headers={"Origin": settings.FRONTEND_HOST},
        )
    assert response.status_code == 200
    assert response.json() == {
        "answer": "provider-only-answer",
        "request_id": response.headers["X-Request-ID"],
    }
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["messages"][1]["content"] == "user-private-question"
    assert "没有读取" in payload["messages"][0]["content"]
    assert payload["max_tokens"] == 1024
    assert payload["model"] == settings.DEEPSEEK_MODEL
    assert payload["thinking"] == {"type": "disabled"}
    assert client_args["max_retries"] == 0
    assert client_args["timeout"].connect == 5
    assert requests[0].extensions["timeout"]["read"] <= 30
    assert "test-only-secret" not in caplog.text
    assert "user-private-question" not in caplog.text
    assert "provider-only-answer" not in caplog.text
    assert "prompt_tokens=10" in caplog.text


@pytest.mark.parametrize("message", ["", "   \n\t", "a" * 4001])
def test_invalid_input_has_request_id(
    authenticated_client: TestClient, message: str
) -> None:
    response = authenticated_client.post(CHAT_URL, json={"message": message})
    assert response.status_code == 422
    assert UUID(response.headers["X-Request-ID"])


def test_maximum_input_is_valid_after_stripping() -> None:
    assert len(chat.ChatRequest(message="  " + "a" * 4000 + "  ").message) == 4000


def test_missing_authentication_has_request_id(client: TestClient) -> None:
    response = client.post(CHAT_URL, json={"message": "你好"})
    assert response.status_code == 401
    assert UUID(response.headers["X-Request-ID"])


def test_missing_key(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", None)
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        503,
        "model_not_configured",
    )


@pytest.mark.parametrize(
    "upstream_status,status,code,attempts",
    [
        (401, 503, "model_authentication_failed", 1),
        (403, 503, "model_authentication_failed", 1),
        (400, 502, "model_unavailable", 1),
        (429, 429, "model_rate_limited", 2),
        (500, 502, "model_unavailable", 2),
        (503, 502, "model_unavailable", 2),
    ],
)
def test_provider_status_and_retry_limit(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
    status: int,
    code: str,
    attempts: int,
) -> None:
    del model_config
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(upstream_status, json={"error": "test-only-secret"})

    mock_provider(monkeypatch, respond)
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        status,
        code,
    )
    assert calls == attempts


@pytest.mark.parametrize(
    "exception,status,code,attempts",
    [
        (httpx.ConnectError, 502, "model_unavailable", 2),
        (httpx.ConnectTimeout, 504, "model_timeout", 2),
        (httpx.ReadTimeout, 504, "model_timeout", 1),
        (httpx.WriteError, 502, "model_unavailable", 1),
    ],
)
def test_network_failure_classification(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
    exception: type[httpx.RequestError],
    status: int,
    code: str,
    attempts: int,
) -> None:
    del model_config
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise exception("test-only-secret", request=request)

    mock_provider(monkeypatch, respond)
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        status,
        code,
    )
    assert calls == attempts


@pytest.mark.parametrize("answer", ["", " \n ", None])
def test_empty_answer_is_not_retried(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
    answer: str | None,
) -> None:
    del model_config
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = completion_response()
        body["choices"][0]["message"]["content"] = answer
        return httpx.Response(200, json=body)

    mock_provider(monkeypatch, respond)
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        502,
        "model_invalid_response",
    )
    assert calls == 1


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": None},
        {"choices": {"wrong-shape": "choice"}},
        {"choices": [{"message": None}]},
        "invalid-json",
    ],
)
def test_malformed_response_is_not_retried(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
    body: Any,
) -> None:
    del model_config
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if body == "invalid-json":
            return httpx.Response(200, text="invalid-json")
        return httpx.Response(200, json=body)

    mock_provider(monkeypatch, respond)
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        502,
        "model_invalid_response",
    )
    assert calls == 1


def test_total_timeout_includes_both_attempts(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del model_config
    monkeypatch.setattr(settings, "CHAT_TOTAL_TIMEOUT_SECONDS", 0.1)
    calls = 0

    async def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.03)
            return httpx.Response(500, json={"error": "mock"})
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion_response())

    mock_provider(monkeypatch, respond)
    started_at = monotonic()
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        504,
        "model_timeout",
    )
    assert monotonic() - started_at < 0.4
    assert calls == 2


def test_total_timeout_includes_retry_backoff(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del model_config
    monkeypatch.setattr(settings, "CHAT_TOTAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(chat, "RETRY_DELAY_SECONDS", 1)
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "mock"})

    mock_provider(monkeypatch, respond)
    started_at = monotonic()
    assert_error(
        authenticated_client.post(CHAT_URL, json={"message": "你好"}),
        504,
        "model_timeout",
    )
    assert monotonic() - started_at < 0.3
    assert calls == 1


def test_retry_can_recover_successfully(
    authenticated_client: TestClient,
    model_config: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del model_config
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "mock"})
        return httpx.Response(200, json=completion_response())

    mock_provider(monkeypatch, respond)
    response = authenticated_client.post(CHAT_URL, json={"message": "你好"})
    assert response.status_code == 200
    assert calls == 2


def test_unexpected_error_and_method_error_keep_request_id(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected_error(_message: str, _request_id: str) -> str:
        raise RuntimeError("test-only-secret")

    monkeypatch.setattr("app.api.routes.chat.generate_answer", unexpected_error)
    response = authenticated_client.post(
        CHAT_URL,
        json={"message": "你好"},
        headers={"Origin": settings.FRONTEND_HOST},
    )
    assert_error(response, 500, "internal_error")
    assert response.headers["Access-Control-Allow-Origin"] == settings.FRONTEND_HOST
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    response = authenticated_client.get(CHAT_URL)
    assert response.status_code == 405
    assert UUID(response.headers["X-Request-ID"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("CHAT_TOTAL_TIMEOUT_SECONDS", 31),
        ("CHAT_CONNECT_TIMEOUT_SECONDS", 6),
        ("CHAT_MAX_TOKENS", 1025),
        ("CHAT_TOTAL_TIMEOUT_SECONDS", 0),
    ],
)
def test_settings_enforce_budget_caps(field: str, value: int) -> None:
    values = settings.model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        Settings(**values)
