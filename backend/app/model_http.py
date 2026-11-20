"""Keep explicit loopback model connections independent of shell proxies."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
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


def model_client_options(client: httpx.AsyncClient | None) -> dict[str, Any]:
    """把可选的回环 HTTP 客户端转成 openai 客户端的关键字参数。

    公网地址必须保持不传 http_client，让 openai 库自己建连接；只有回环地址才
    需要注入 trust_env=False 的客户端来绕开 shell 代理。值类型写成 Any，是因为
    AsyncOpenAI(**options) 的每个关键字参数都来自这一份可变字典，收窄成
    dict[str, AsyncClient] 会让 strict mypy 逐个关键字参数报不兼容。
    """
    return {} if client is None else {"http_client": client}
