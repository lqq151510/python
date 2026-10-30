import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlmodel import Session

from app import semantic
from app.core.config import Settings, settings
from tests.api.routes.test_chat import completion_response, mock_provider
from tests.api.routes.test_documents import URL, headers, upload
from tests.utils.user import create_random_user


@pytest.mark.parametrize(
    "payload",
    [
        {"hits": [{"id": "foreign", "score": 0.9}]},
        {"hits": [{"id": "a", "score": float("nan")}]},
        {"hits": [{"id": "a", "score": True}]},
        {"hits": [{"id": "a", "score": 2}]},
        {"hits": [{"id": "a", "score": 0.9}, {"id": "a", "score": 0.8}]},
        {"hits": "invalid"},
    ],
)
def test_rejects_foreign_duplicate_and_invalid_worker_hits(payload: dict) -> None:
    with pytest.raises(semantic.SemanticUnavailable):
        semantic.validate_hits(payload, ["a", "b"], 5)


def test_threshold_and_rank_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RAG_MIN_SIMILARITY", 0.3)
    assert semantic.validate_hits(
        {"hits": [{"id": "a", "score": 0.1}, {"id": "b", "score": 0.8}]}, ["a", "b"], 5
    ) == [(1, 0.8)]
    result = semantic.fuse_rankings([(0, 100), (1, 40)], [(1, 0.9), (2, 0.8)], limit=3)
    assert [index for index, _ in result] == [1, 0, 2]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://127.0.0.1/redirect",
        "http://user:password@127.0.0.1",
    ],
)
def test_worker_url_is_local_and_has_no_redirect_path(url: str) -> None:
    with pytest.raises(ValueError):
        Settings._local_rag_worker(HttpUrl(url))


def test_worker_rpc_is_authenticated_and_does_not_use_environment_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = tmp_path / "worker.token"
    token.write_text("test-only-worker-token-" * 3)
    monkeypatch.setattr(semantic, "TOKEN_PATH", token)
    monkeypatch.setattr(settings, "RAG_WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "RAG_CACHE_ENABLED", False)
    real_client = httpx.Client
    identity = {"space_id": "a" * 64, "protocol_id": "b" * 64, "generation": "c" * 32}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.headers["Authorization"] == "Bearer " + token.read_text()
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ready", "identity": identity})
        payload = json.loads(request.content)
        assert payload["chunks"] == [{"id": "a", "text": "authorized-only"}]
        assert payload["expected_identity"] == identity
        return httpx.Response(200, json={"hits": [{"id": "a", "score": 0.9}], "identity": identity})

    def make_client(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(semantic.httpx, "Client", make_client)
    assert semantic.vector_rank("query", ["a"], ["authorized-only"], 5) == [(0, 0.9)]


def test_vector_search_is_scoped_in_sql_before_worker_call(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, other = create_random_user(db), create_random_user(db)
    first = upload(
        client, owner, "# Redis\n缓存失效后重新读取数据库。", "cache.md"
    ).json()["document"]
    upload(client, owner, "# Password\nNever expose unrelated credentials.", "other.md")
    foreign = upload(client, other, "other-user-private-fragment", "private.md").json()[
        "document"
    ]
    calls = []

    def rank(_query, identifiers, texts, _limit, *_extra):
        calls.append(identifiers)
        assert len(texts) == 1 and "缓存失效" in texts[0]
        assert "credentials" not in texts[0] and "private-fragment" not in texts[0]
        return [(0, 0.9)]

    monkeypatch.setattr(semantic, "vector_rank", rank)
    response = client.post(
        URL + "/search",
        headers=headers(owner),
        json={
            "query": "过期数据怎么更新",
            "method": "vector",
            "document_id": first["id"],
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["method"] == "vector" and result["warning"] is None
    assert result["sources"][0]["document_id"] == first["id"]
    assert result["sources"][0]["line_start"] == 1
    before = len(calls)
    for document_id in [foreign["id"], "99999999-9999-9999-9999-999999999999"]:
        assert (
            client.post(
                URL + "/search",
                headers=headers(owner),
                json={"query": "cache", "method": "vector", "document_id": document_id},
            ).status_code
            == 404
        )
    assert len(calls) == before
    assert (
        client.delete(URL + "/" + first["id"], headers=headers(owner)).status_code
        == 200
    )
    assert (
        client.post(
            URL + "/search",
            headers=headers(owner),
            json={"query": "cache", "method": "vector", "document_id": first["id"]},
        ).status_code
        == 404
    )
    assert len(calls) == before


def test_hybrid_fallback_is_explicit_and_vector_does_not_silently_fallback(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = create_random_user(db)
    upload(client, owner, "# Cache\nRedis cache expiry.")

    def unavailable(*_args):
        raise semantic.SemanticUnavailable("vector_timeout")

    monkeypatch.setattr(semantic, "vector_rank", unavailable)
    response = client.post(
        URL + "/search",
        headers=headers(owner),
        json={"query": "Redis", "method": "hybrid"},
    )
    assert response.status_code == 200
    assert response.json()["method"] == "bm25"
    assert response.json()["warning"] == "vector_timeout"
    assert response.json()["sources"]
    response = client.post(
        URL + "/search",
        headers=headers(owner),
        json={"query": "Redis", "method": "vector"},
    )
    assert (
        response.status_code == 503
        and response.json()["detail"]["code"] == "vector_timeout"
    )


def test_chat_uses_selected_retriever_and_returns_method(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = create_random_user(db)
    upload(client, owner, "# Cache\nRedis cache expiry.")
    monkeypatch.setattr(semantic, "vector_rank", lambda *args: [(0, 0.9)])
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", SecretStr("test-only-secret"))
    mock_provider(
        monkeypatch,
        lambda request: httpx.Response(
            200, json=completion_response("Redis cache expiry. [S1]")
        ),
    )
    response = client.post(
        "/api/v1/chat",
        headers=headers(owner),
        json={
            "message": "缓存过期",
            "use_documents": True,
            "retrieval_method": "hybrid",
        },
    )
    assert response.status_code == 200
    assert response.json()["retrieval_method"] == "hybrid"
    assert response.json()["sources"][0]["citation"] == "S1"


@pytest.mark.skipif(
    os.environ.get("VERIFY_REAL_RAG") != "1", reason="Opt-in real local worker"
)
def test_real_worker_end_to_end_with_owner_scope_and_source_mapping(
    client: TestClient, db: Session
) -> None:
    owner, other = create_random_user(db), create_random_user(db)
    auth_doc = upload(
        client,
        owner,
        "# 登录\n访问令牌过期后，需要用户重新登录，服务端会拒绝失效的令牌。",
        "auth.md",
    ).json()["document"]
    cache_doc = upload(
        client,
        owner,
        "# 缓存\nRedis 为热点查询保存结果，减少数据库查询次数。",
        "cache.md",
    ).json()["document"]
    foreign = upload(
        client, other, "# 私有\nother-owner-only-content", "private.md"
    ).json()["document"]
    response = client.post(
        URL + "/search",
        headers=headers(owner),
        json={"query": "登录凭据失效之后该怎么操作", "method": "vector"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["method"] == "vector"
    assert response.json()["sources"][0]["document_id"] == auth_doc["id"]
    assert all(
        source["document_id"] != foreign["id"] for source in response.json()["sources"]
    )
    response = client.post(
        URL + "/search",
        headers=headers(owner),
        json={
            "query": "缓存热点查询",
            "method": "hybrid",
            "document_id": cache_doc["id"],
        },
    )
    assert response.status_code == 200
    assert response.json()["method"] == "hybrid" and response.json()["warning"] is None
    assert {source["document_id"] for source in response.json()["sources"]} == {
        cache_doc["id"]
    }
    assert response.json()["sources"][0]["line_start"] == 1
