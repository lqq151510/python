import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pydantic import SecretStr
from sqlmodel import Session

from app import image_library, web_agent
from app.core.config import settings
from app.core.security import create_access_token
from app.models import User
from tests.api.routes.test_chat import completion_response
from tests.utils.user import create_random_user

IMAGES = f"{settings.API_V1_STR}/images"
AGENT = f"{settings.API_V1_STR}/agent"
DOCUMENTS = f"{settings.API_V1_STR}/documents"
JPEG = b"\xff\xd8\xfffake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nfake-png"


def headers(user: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + create_access_token(user.id, timedelta(minutes=5))
    }


def test_image_and_agent_require_login(client: TestClient) -> None:
    assert client.get(IMAGES).status_code == 401
    assert (
        client.post(IMAGES, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code
        == 401
    )
    assert client.post(IMAGES + "/search", json={"query": "药品"}).status_code == 401
    assert (
        client.post(
            IMAGES + "/search-by-image", files={"file": ("a.jpg", JPEG, "image/jpeg")}
        ).status_code
        == 401
    )
    assert client.get(AGENT + "/sessions").status_code == 401
    assert client.post(AGENT + "/turns", json={"message": "你好"}).status_code == 401


def test_images_stay_with_their_owner_and_empty_search_skips_the_model(
    client: TestClient,
    db: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "IMAGE_LIBRARY_DIR", tmp_path)
    calls = {"text": 0, "image": 0}

    def embed_image(raw: bytes) -> list[float]:
        calls["image"] += 1
        return [1.0, 0.0] if raw.startswith(b"\xff\xd8\xff") else [0.0, 1.0]

    def embed_text(text: str) -> list[float]:
        calls["text"] += 1
        assert text
        return [1.0, 0.0]

    monkeypatch.setattr(image_library, "embed_image", embed_image)
    monkeypatch.setattr(image_library, "embed_text", embed_text)
    owner, other = create_random_user(db), create_random_user(db)
    empty = client.post(
        IMAGES + "/search", headers=headers(owner), json={"query": "药品"}
    )
    assert empty.status_code == 200
    assert empty.json()["hits"] == []
    assert calls == {"text": 0, "image": 0}
    uploaded = client.post(
        IMAGES,
        headers=headers(owner),
        files={"file": ("owner-only.jpg", JPEG, "image/jpeg")},
    )
    assert uploaded.status_code == 201
    image_id = uploaded.json()["image"]["id"]
    assert (
        client.post(
            IMAGES,
            headers=headers(other),
            files={"file": ("other.png", PNG, "image/png")},
        ).status_code
        == 201
    )
    owner_hits = client.post(
        IMAGES + "/search", headers=headers(owner), json={"query": "我的图片"}
    ).json()["hits"]
    assert [hit["filename"] for hit in owner_hits] == ["owner-only.jpg"]
    assert "embedding" not in owner_hits[0]
    other_hits = client.post(
        IMAGES + "/search", headers=headers(other), json={"query": "我的图片"}
    ).json()["hits"]
    assert all(hit["filename"] != "owner-only.jpg" for hit in other_hits)
    listed = client.get(IMAGES, headers=headers(other)).json()["data"]
    assert [item["filename"] for item in listed] == ["other.png"]
    assert (
        client.get(IMAGES + f"/{image_id}/file", headers=headers(other)).status_code
        == 404
    )
    similar = client.post(
        IMAGES + "/search-by-image",
        headers=headers(owner),
        files={"file": ("query.jpg", JPEG, "image/jpeg")},
    )
    assert similar.status_code == 200
    assert [hit["filename"] for hit in similar.json()["hits"]] == ["owner-only.jpg"]
    stranger = client.post(
        IMAGES + "/search-by-image",
        headers=headers(other),
        files={"file": ("query.jpg", JPEG, "image/jpeg")},
    )
    assert all(hit["filename"] != "owner-only.jpg" for hit in stranger.json()["hits"])
    alone = create_random_user(db)
    before = calls["image"]
    assert (
        client.post(
            IMAGES + "/search-by-image",
            headers=headers(alone),
            files={"file": ("query.jpg", JPEG, "image/jpeg")},
        ).json()["hits"]
        == []
    )
    assert calls["image"] == before


def test_agent_empty_result_does_not_call_the_model(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = create_random_user(db)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("empty result must not call the model")

    monkeypatch.setattr(web_agent, "AsyncOpenAI", explode)
    monkeypatch.setattr(image_library, "embed_text", explode)
    monkeypatch.setattr(image_library, "embed_image", explode)
    response = client.post(
        AGENT + "/turns", headers=headers(user), json={"message": "库里有什么"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn"]["answer"] == "库里没有相关内容。"
    assert body["turn"]["steps"][0]["name"] == "search_knowledge"
    assert body["turn"]["stopped"] is False
    assert body["request_id"]


def test_agent_stops_after_five_steps_and_does_not_run_write_tools(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = create_random_user(db)
    uploaded = client.post(
        DOCUMENTS,
        headers=headers(user),
        files={
            "file": (
                "guide.md",
                "FastAPI 异步请求不阻塞事件循环。".encode(),
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SecretStr("test-only-secret"))
    monkeypatch.setattr(web_agent, "RETRY_DELAY_SECONDS", 0.01)
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        payload = completion_response("")
        payload["choices"][0]["finish_reason"] = "tool_calls"
        payload["choices"][0]["message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{calls['count']}",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "/etc/passwd", "command": "rm -rf /"}
                        ),
                    },
                }
            ],
        }
        return httpx.Response(200, json=payload)

    def create_client(**kwargs: Any) -> AsyncOpenAI:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return AsyncOpenAI(**kwargs)

    monkeypatch.setattr(web_agent, "AsyncOpenAI", create_client)
    response = client.post(
        AGENT + "/turns", headers=headers(user), json={"message": "FastAPI 异步"}
    )
    assert response.status_code == 200
    body = response.json()
    assert calls["count"] == 5
    assert body["turn"]["stopped"] is True
    assert "最大步数" in body["turn"]["answer"]
    assert len(body["turn"]["steps"]) == 6
    assert all(
        step["name"] == "write_file" and step["ok"] is False
        for step in body["turn"]["steps"][1:]
    )
    assert "未知工具" in body["turn"]["steps"][1]["detail"]
    assert "/etc/passwd" not in body["turn"]["answer"]


def test_agent_sessions_belong_to_one_user_and_keep_turns(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, other = create_random_user(db), create_random_user(db)
    uploaded = client.post(
        DOCUMENTS,
        headers=headers(owner),
        files={
            "file": (
                "guide.md",
                "FastAPI 异步请求不阻塞事件循环。".encode(),
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 201
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SecretStr("test-only-secret"))
    monkeypatch.setattr(web_agent, "RETRY_DELAY_SECONDS", 0.01)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        answer = "第一轮提到了 FastAPI" if len(seen) == 1 else "第二轮接着说"
        return httpx.Response(200, json=completion_response(answer))

    def create_client(**kwargs: Any) -> AsyncOpenAI:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return AsyncOpenAI(**kwargs)

    monkeypatch.setattr(web_agent, "AsyncOpenAI", create_client)
    first = client.post(
        AGENT + "/turns", headers=headers(owner), json={"message": "FastAPI 异步"}
    )
    assert first.status_code == 200
    session_id = first.json()["session"]["id"]
    second = client.post(
        AGENT + "/turns",
        headers=headers(owner),
        json={"message": "刚才那个呢", "session_id": session_id},
    )
    assert second.status_code == 200
    assert second.json()["session"]["turn_count"] == 2
    contents = [item.get("content") for item in seen[1]["messages"]]
    assert "FastAPI 异步" in contents
    assert "第一轮提到了 FastAPI" in contents
    assert client.get(AGENT + "/sessions", headers=headers(other)).json()["data"] == []
    assert (
        client.get(
            AGENT + f"/sessions/{session_id}", headers=headers(other)
        ).status_code
        == 404
    )
    stolen = client.post(
        AGENT + "/turns",
        headers=headers(other),
        json={"message": "FastAPI 异步", "session_id": session_id},
    )
    assert stolen.status_code == 404
    detail = client.get(AGENT + f"/sessions/{session_id}", headers=headers(owner))
    assert [turn["message"] for turn in detail.json()["turns"]] == [
        "FastAPI 异步",
        "刚才那个呢",
    ]
