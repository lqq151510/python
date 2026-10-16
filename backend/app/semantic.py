"""Narrow local RPC: only IDs from the authorized SQL snapshot may be returned."""

import math
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings

TOKEN_PATH = Path(__file__).resolve().parents[2] / ".local/zebao-rag/worker.token"
MAX_VECTOR_CHUNKS = 256


class SemanticUnavailable(Exception):
    def __init__(self, code: str = "vector_unavailable") -> None:
        self.code = code
        super().__init__(code)


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
    if len(identifiers) > MAX_VECTOR_CHUNKS:
        raise SemanticUnavailable("vector_capacity")
    engine = engine or settings.RAG_VECTOR_STORE
    cache_key = retrieval_cache.key_for(
        namespace, engine, query, identifiers, texts, limit
    )
    cached, status = retrieval_cache.get(cache_key, namespace)
    if cache_info is not None:
        cache_info["status"] = status
    if cached is not None:
        try:
            return validate_hits(cached, identifiers, limit)
        except SemanticUnavailable:
            pass
    try:
        token = TOKEN_PATH.read_text().strip()
        if len(token) < 32:
            raise SemanticUnavailable()
        # No proxy environment, redirects or retries: scoped source text stays local.
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = client.post(
                str(settings.RAG_WORKER_URL).rstrip("/") + "/rank",
                json={
                    "query": query,
                    "namespace": namespace,
                    "engine": engine,
                    "chunks": [
                        {"id": identifier, "text": text}
                        for identifier, text in zip(identifiers, texts, strict=True)
                    ],
                    "limit": limit,
                },
                headers={"Authorization": "Bearer " + token},
                timeout=httpx.Timeout(settings.RAG_WORKER_TIMEOUT_SECONDS, connect=2),
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException:
        raise SemanticUnavailable("vector_timeout") from None
    except OSError, httpx.HTTPError, ValueError:
        raise SemanticUnavailable() from None
    ranked = validate_hits(payload, identifiers, limit)
    retrieval_cache.put(cache_key, payload)
    return ranked


def validate_hits(
    payload: Any, identifiers: list[str], limit: int
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
        if score >= settings.RAG_MIN_SIMILARITY:
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
