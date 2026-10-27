"""Narrow local RPC: only IDs from the authorized SQL snapshot may be returned."""

import math
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from app.core.config import settings
from app.core.observability import observe_operation

TOKEN_PATH = Path(__file__).resolve().parents[2] / ".local/zebao-rag/worker.token"
MAX_VECTOR_CHUNKS = 256
# Match the upload quota: at most 20 documents, each with 256 chunks.
MAX_VECTOR_CANDIDATES = 20 * MAX_VECTOR_CHUNKS


class SemanticUnavailable(Exception):
    def __init__(self, code: str = "vector_unavailable") -> None:
        self.code = code
        super().__init__(code)


@observe_operation("vector_rank")
def vector_rank(
    query: str,
    identifiers: list[str],
    texts: list[str],
    limit: int,
    namespace: str = "local-experiment",
    engine: str | None = None,
    cache_info: dict[str, str] | None = None,
) -> list[tuple[int, float]]:
    from app import retrieval_cache

    if not settings.RAG_WORKER_ENABLED:
        raise SemanticUnavailable("vector_disabled")
    if len(identifiers) > MAX_VECTOR_CANDIDATES:
        raise SemanticUnavailable("vector_capacity")
    if len(identifiers) != len(texts) or len(set(identifiers)) != len(identifiers):
        raise SemanticUnavailable()
    if not identifiers:
        return []
    engine = engine or settings.RAG_VECTOR_STORE
    cache_key = retrieval_cache.key_for(
        namespace,
        engine,
        query,
        identifiers,
        texts,
        limit,
        identity=settings.RAG_EMBEDDING_IDENTITY,
    )
    cached, status = retrieval_cache.get(cache_key, namespace)
    if cache_info is not None:
        cache_info["status"] = status
    if cached is not None:
        try:
            return validate_hits(cached, identifiers, limit)
        except SemanticUnavailable:
            pass
    deadline = monotonic() + settings.RAG_WORKER_TIMEOUT_SECONDS
    ranked: list[tuple[int, float]] = []
    try:
        token = TOKEN_PATH.read_text().strip()
        if len(token) < 32:
            raise SemanticUnavailable()
        # No proxy environment, redirects or retries: scoped source text stays local.
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            for start in range(0, len(identifiers), MAX_VECTOR_CHUNKS):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise SemanticUnavailable("vector_timeout")
                batch_ids = identifiers[start : start + MAX_VECTOR_CHUNKS]
                batch_texts = texts[start : start + MAX_VECTOR_CHUNKS]
                response = client.post(
                    str(settings.RAG_WORKER_URL).rstrip("/") + "/rank",
                    json={
                        "query": query,
                        "namespace": namespace,
                        "engine": engine,
                        "chunks": [
                            {"id": identifier, "text": text}
                            for identifier, text in zip(
                                batch_ids, batch_texts, strict=True
                            )
                        ],
                        "limit": limit,
                    },
                    headers={"Authorization": "Bearer " + token},
                    timeout=httpx.Timeout(remaining, connect=min(2, remaining)),
                )
                response.raise_for_status()
                batch_ranked = validate_hits(
                    response.json(), batch_ids, limit, apply_threshold=False
                )
                if monotonic() >= deadline:
                    raise SemanticUnavailable("vector_timeout")
                ranked.extend((start + index, score) for index, score in batch_ranked)
    except httpx.TimeoutException:
        raise SemanticUnavailable("vector_timeout") from None
    except OSError, httpx.HTTPError, ValueError:
        raise SemanticUnavailable() from None
    ranked = sorted(ranked, key=lambda item: (-item[1], item[0]))[:limit]
    # Cache only the complete snapshot's global top K, never a partial batch.
    payload = {
        "hits": [{"id": identifiers[index], "score": score} for index, score in ranked]
    }
    retrieval_cache.put(cache_key, payload)
    return validate_hits(payload, identifiers, limit)


def validate_hits(
    payload: Any, identifiers: list[str], limit: int, *, apply_threshold: bool = True
) -> list[tuple[int, float]]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list) or len(hits) > limit:
        raise SemanticUnavailable()
    allowed = {identifier: index for index, identifier in enumerate(identifiers)}
    seen = set()
    ranked = []
    for hit in hits:
        if not isinstance(hit, dict):
            raise SemanticUnavailable()
        identifier, score = hit.get("id"), hit.get("score")
        if (
            not isinstance(identifier, str)
            or identifier not in allowed
            or identifier in seen
        ):
            raise SemanticUnavailable()
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not -1 <= score <= 1
        ):
            raise SemanticUnavailable()
        seen.add(identifier)
        if not apply_threshold or score >= settings.RAG_MIN_SIMILARITY:
            ranked.append((allowed[identifier], float(score)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def fuse_rankings(
    *rankings: list[tuple[int, float]], limit: int
) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: BM25 scores and cosine values use different scales."""
    totals: dict[int, float] = {}
    for ranking in rankings:
        for rank, (index, _) in enumerate(ranking, 1):
            totals[index] = totals.get(index, 0.0) + 1.0 / (60 + rank)
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
