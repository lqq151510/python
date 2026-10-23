"""Redis stores short-lived hit IDs and scores, never full source fragments."""

import hashlib
import json
from functools import lru_cache
from typing import Any, cast

from redis import Redis, RedisError

from app.core.config import settings

TTL_SECONDS = 180


@lru_cache(maxsize=1)
def client() -> Redis:
    return Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )


def key_for(
    namespace: str,
    engine: str,
    query: str,
    identifiers: list[str],
    texts: list[str],
    limit: int,
    identity: str | None = None,
) -> str:
    # A new document snapshot gets a new key; deleted documents cannot reuse hits.
    # identity 区分嵌入模型版本（含 LoRA 适配器），否则微调前后会互相复用命中。
    fingerprint = json.dumps(
        [
            identity or settings.RAG_EMBEDDING_IDENTITY,
            namespace,
            engine,
            query,
            identifiers,
            texts,
            limit,
        ],
        ensure_ascii=False,
    )
    return (
        f"devassist:retrieval:{namespace}:"
        + hashlib.sha256(fingerprint.encode()).hexdigest()
    )


def get(key: str, namespace: str) -> tuple[dict[str, Any] | None, str]:
    if not settings.RAG_CACHE_ENABLED:
        return None, "disabled"
    try:
        raw = cast(str | None, client().get(key))
        status = "hit" if raw else "miss"
        counter = f"devassist:cache:{namespace}:{status}"
        client().incr(counter)
        client().expire(counter, 3600)
        payload = json.loads(raw) if raw else None
        return payload if isinstance(payload, dict) else None, status
    except (RedisError, ValueError, TypeError):
        return None, "unavailable"


def put(key: str, payload: dict[str, Any]) -> None:
    if settings.RAG_CACHE_ENABLED:
        try:
            client().setex(key, TTL_SECONDS, json.dumps(payload))
        except RedisError:
            pass


def stats(namespace: str) -> dict[str, int]:
    values = cast(
        list[str | None],
        client().mget(
            [f"devassist:cache:{namespace}:{kind}" for kind in ("hit", "miss")]
        ),
    )
    return {
        kind: int(value or 0)
        for kind, value in zip(("hit", "miss"), values, strict=True)
    }
