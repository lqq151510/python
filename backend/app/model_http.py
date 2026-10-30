"""Keep explicit loopback model connections independent of shell proxies."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx


def is_loopback_model_url(base_url: str) -> bool:
    try:
        return urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return False


@asynccontextmanager
async def local_model_http_client(base_url: str) -> AsyncIterator[httpx.AsyncClient | None]:
    if is_loopback_model_url(base_url):
        async with httpx.AsyncClient(trust_env=False) as client:
            yield client
    else:
        yield None
