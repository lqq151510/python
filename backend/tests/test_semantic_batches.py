"""Offline RPC tests; run with --noconftest to avoid database fixtures/.env."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest

import app

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rpc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    # Import the real RPC/cache code with inert settings, never app.core.config.
    config = ModuleType("app.core.config")
    config.settings = SimpleNamespace(  # type: ignore[attr-defined]
        RAG_WORKER_ENABLED=True,
        RAG_WORKER_URL="http://127.0.0.1:8765",
        RAG_WORKER_TIMEOUT_SECONDS=30.0,
        RAG_MIN_SIMILARITY=0.3,
        RAG_VECTOR_STORE="milvus",
        RAG_EMBEDDING_IDENTITY="offline-test-model",
    )
    monkeypatch.setitem(sys.modules, "app.core.config", config)
    cache = load_module("offline_retrieval_cache", ROOT / "app/retrieval_cache.py")
    monkeypatch.setattr(app, "retrieval_cache", cache, raising=False)
    semantic = load_module("offline_semantic", ROOT / "app/semantic.py")
    token = tmp_path / "worker.token"
    token.write_text("offline-worker-token-" * 3)
    monkeypatch.setattr(semantic, "TOKEN_PATH", token)
    stored: dict[str, dict[str, Any]] = {}
    reads: list[tuple[str, str]] = []
    writes: list[str] = []

    def get(key: str, namespace: str) -> tuple[dict[str, Any] | None, str]:
        reads.append((key, namespace))
        return stored.get(key), "hit" if key in stored else "miss"

    def put(key: str, payload: dict[str, Any]) -> None:
        writes.append(key)
        stored[key] = payload

    monkeypatch.setattr(cache, "get", get)
    monkeypatch.setattr(cache, "put", put)
    calls: list[dict[str, Any]] = []
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(semantic, "monotonic", lambda: clock.now)
    real_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.headers["Authorization"] == "Bearer " + token.read_text()
        if request.method == "GET":
            return harness.health()
        body = json.loads(request.content)
        body["timeout"] = request.extensions["timeout"]
        calls.append(body)
        response = harness.respond(body)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
            if isinstance(payload, dict) and "hits" in payload and "identity" not in payload:
                payload["identity"] = harness.identity.copy()
                return httpx.Response(200, json=payload)
        return response

    def make_client(**kwargs: Any) -> httpx.Client:
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(semantic.httpx, "Client", make_client)
    harness = SimpleNamespace(
        semantic=semantic,
        settings=config.settings,  # type: ignore[attr-defined]
        cache=cache,
        stored=stored,
        reads=reads,
        writes=writes,
        calls=calls,
        clock=clock,
        identity={"space_id": "a" * 64, "protocol_id": "b" * 64, "generation": "c" * 32},
        health=lambda: httpx.Response(200, json={"status": "ready", "identity": harness.identity.copy()}),
        respond=lambda _body: httpx.Response(200, json={"hits": []}),
    )
    return harness


def candidates(count: int = 260) -> tuple[list[str], list[str]]:
    return [f"c-{i}" for i in range(count)], [f"authorized-{i}" for i in range(count)]


def rank(rpc: SimpleNamespace, limit: int = 2, engine: str = "milvus") -> Any:
    identifiers, texts = candidates()
    return rpc.semantic.vector_rank(
        "query", identifiers, texts, limit, "owner-a", engine
    )


@pytest.mark.parametrize("engine", ["milvus", "chroma"])
def test_global_top_k_includes_later_batch_and_preserves_scope(
    rpc: SimpleNamespace, engine: str
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        assert body["namespace"] == "owner-a" and body["engine"] == engine
        ids = [chunk["id"] for chunk in body["chunks"]]
        assert len(ids) <= rpc.semantic.MAX_VECTOR_CHUNKS
        assert all(
            chunk["text"] == "authorized-" + chunk["id"].removeprefix("c-")
            for chunk in body["chunks"]
        )
        hits = (
            [{"id": "c-0", "score": 0.8}, {"id": "c-1", "score": 0.7}]
            if "c-0" in ids
            else [{"id": "c-259", "score": 0.95}, {"id": "c-256", "score": 0.6}]
        )
        return httpx.Response(200, json={"hits": hits})

    rpc.respond = respond
    assert rank(rpc, engine=engine) == [(259, 0.95), (0, 0.8)]
    assert [len(call["chunks"]) for call in rpc.calls] == [256, 4]
    assert len(rpc.writes) == 1
    assert rpc.stored[rpc.writes[0]] == {
        "hits": [{"id": "c-259", "score": 0.95}, {"id": "c-0", "score": 0.8}]
    }


def test_equal_scores_use_global_candidate_order(rpc: SimpleNamespace) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        ids = [chunk["id"] for chunk in body["chunks"]]
        return httpx.Response(200, json={"hits": [{"id": ids[-1], "score": 0.8}]})

    rpc.respond = respond
    assert rank(rpc) == [(255, 0.8), (259, 0.8)]


@pytest.mark.parametrize("forged_id", ["c-0", "other-owner-secret"])
def test_later_batch_cannot_return_another_batch_or_foreign_id(
    rpc: SimpleNamespace, forged_id: str
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        identifier = "c-0" if body["chunks"][0]["id"] == "c-0" else forged_id
        return httpx.Response(200, json={"hits": [{"id": identifier, "score": 0.9}]})

    rpc.respond = respond
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_unavailable"
    assert len(rpc.calls) == 2 and rpc.writes == []


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid_json", "invalid_hit"])
def test_any_batch_failure_discards_partial_results_and_cache(
    rpc: SimpleNamespace, failure: str
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        if body["chunks"][0]["id"] == "c-0":
            return httpx.Response(200, json={"hits": [{"id": "c-0", "score": 0.9}]})
        if failure == "timeout":
            raise httpx.ReadTimeout("offline timeout")
        if failure == "http":
            return httpx.Response(503)
        if failure == "invalid_json":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json={"hits": [{"id": "c-259", "score": 2}]})

    rpc.respond = respond
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    expected = "vector_timeout" if failure == "timeout" else "vector_unavailable"
    assert caught.value.code == expected
    assert len(rpc.calls) == 2 and rpc.writes == []


def test_later_batches_share_remaining_timeout(rpc: SimpleNamespace) -> None:
    def respond(_body: dict[str, Any]) -> httpx.Response:
        rpc.clock.now += 7
        return httpx.Response(200, json={"hits": []})

    rpc.respond = respond
    assert rank(rpc) == []
    assert [call["timeout"]["read"] for call in rpc.calls] == [30, 23]
    assert [call["timeout"]["connect"] for call in rpc.calls] == [2, 2]


@pytest.mark.parametrize("exhaust_on_batch", [1, 2])
def test_exhausted_total_budget_never_returns_or_caches_partial_results(
    rpc: SimpleNamespace, exhaust_on_batch: int
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        rpc.clock.now += 30 if len(rpc.calls) == exhaust_on_batch else 5
        return httpx.Response(
            200, json={"hits": [{"id": body["chunks"][0]["id"], "score": 0.9}]}
        )

    rpc.respond = respond
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_timeout"
    assert len(rpc.calls) == exhaust_on_batch and rpc.writes == []


def test_complete_cache_replays_and_all_candidates_affect_key(
    rpc: SimpleNamespace,
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        identifier = body["chunks"][-1]["id"]
        return httpx.Response(200, json={"hits": [{"id": identifier, "score": 0.9}]})

    rpc.respond = respond
    expected = rank(rpc)
    assert rank(rpc) == expected and len(rpc.calls) == 2
    assert rpc.reads[0] == rpc.reads[1]
    identifiers, texts = candidates()
    texts[-1] = "updated-last-batch"
    rpc.semantic.vector_rank("query", identifiers, texts, 2, "owner-a", "milvus")
    assert len(rpc.calls) == 4 and rpc.reads[-1][0] != rpc.reads[0][0]


def test_cached_result_never_used_when_worker_identity_unavailable(rpc: SimpleNamespace) -> None:
    assert rank(rpc) == []
    rpc.health = lambda: httpx.Response(503)
    with pytest.raises(rpc.semantic.SemanticUnavailable):
        rank(rpc)
    assert len(rpc.calls) == 2


def test_old_worker_without_identity_is_rejected_before_cache_read(rpc: SimpleNamespace) -> None:
    rpc.health = lambda: httpx.Response(200, json={"status": "ready"})
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_identity_unavailable"
    assert not rpc.reads and not rpc.calls


def test_changed_model_identity_misses_previous_cache(rpc: SimpleNamespace) -> None:
    assert rank(rpc) == []
    prior_key = rpc.reads[-1][0]
    rpc.identity["space_id"] = "d" * 64
    rpc.identity["protocol_id"] = "e" * 64
    rpc.identity["generation"] = "f" * 32
    assert rank(rpc) == []
    assert rpc.reads[-1][0] != prior_key and len(rpc.calls) == 4


def test_worker_restart_between_batches_discards_partial_results(rpc: SimpleNamespace) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        result_identity = rpc.identity.copy()
        if body["chunks"][0]["id"] != "c-0":
            result_identity["generation"] = "d" * 32
        return httpx.Response(200, json={"hits": [], "identity": result_identity})

    rpc.respond = respond
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_identity_changed" and not rpc.writes


def test_worker_restart_during_cache_hit_is_rejected(rpc: SimpleNamespace) -> None:
    assert rank(rpc) == []
    checks = 0

    def health() -> httpx.Response:
        nonlocal checks
        checks += 1
        identity = rpc.identity.copy()
        if checks > 1:
            identity["generation"] = "d" * 32
        return httpx.Response(200, json={"status": "ready", "identity": identity})

    rpc.health = health
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_identity_changed"


def test_short_remaining_budget_skips_redis_read_and_write(rpc: SimpleNamespace) -> None:
    rpc.settings.RAG_WORKER_TIMEOUT_SECONDS = 1.9
    info: dict[str, str] = {}
    identifiers, texts = candidates(2)
    assert rpc.semantic.vector_rank("query", identifiers, texts, 2, "owner-a", "milvus", info) == []
    assert info["status"] == "skipped_budget"
    assert not rpc.reads and not rpc.writes and len(rpc.calls) == 1


def test_cache_read_over_deadline_never_returns_cached_result(rpc: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_get(_key: str, _namespace: str) -> tuple[dict[str, Any] | None, str]:
        rpc.clock.now += rpc.settings.RAG_WORKER_TIMEOUT_SECONDS
        return {"hits": [{"id": "c-0", "score": 0.9}]}, "hit"

    monkeypatch.setattr(rpc.cache, "get", slow_get)
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rank(rpc)
    assert caught.value.code == "vector_timeout"
    assert not rpc.calls and not rpc.writes


def test_cache_replay_applies_current_similarity_threshold(
    rpc: SimpleNamespace,
) -> None:
    def respond(body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, json={"hits": [{"id": body["chunks"][0]["id"], "score": 0.5}]}
        )

    rpc.respond = respond
    rpc.settings.RAG_MIN_SIMILARITY = 0.8
    assert rank(rpc) == []
    rpc.settings.RAG_MIN_SIMILARITY = 0.3
    assert rank(rpc) == [(0, 0.5), (256, 0.5)]
    assert len(rpc.calls) == 2


def test_forged_cache_hit_is_recomputed(rpc: SimpleNamespace) -> None:
    assert rank(rpc) == []
    rpc.stored[rpc.writes[0]] = {"hits": [{"id": "foreign", "score": 0.9}]}
    assert rank(rpc) == [] and len(rpc.calls) == 4


def test_maximum_document_quota_is_supported(rpc: SimpleNamespace) -> None:
    identifiers, texts = candidates(5120)
    assert rpc.semantic.vector_rank("query", identifiers, texts, 5) == []
    assert len(rpc.calls) == 20
    assert all(len(call["chunks"]) == 256 for call in rpc.calls)


@pytest.mark.parametrize("invalid", ["over_capacity", "mismatched", "duplicate"])
def test_invalid_snapshot_is_rejected_before_rpc_or_cache(
    rpc: SimpleNamespace, invalid: str
) -> None:
    identifiers, texts = candidates(5121 if invalid == "over_capacity" else 260)
    if invalid == "mismatched":
        texts.pop()
    if invalid == "duplicate":
        identifiers[-1] = identifiers[0]
    with pytest.raises(rpc.semantic.SemanticUnavailable) as caught:
        rpc.semantic.vector_rank("query", identifiers, texts, 5)
    expected = "vector_capacity" if invalid == "over_capacity" else "vector_unavailable"
    assert caught.value.code == expected
    assert rpc.calls == [] and rpc.reads == [] and rpc.writes == []


def test_empty_snapshot_does_not_call_worker(rpc: SimpleNamespace) -> None:
    assert rpc.semantic.vector_rank("query", [], [], 5) == []
    assert rpc.calls == [] and rpc.reads == []
