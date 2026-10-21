"""Safe request correlation and terminal events for synchronous operations."""

import json
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from time import monotonic
from typing import ParamSpec, TypeVar
from urllib.parse import urlsplit

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")


_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


def _redact_url(match: re.Match[str]) -> str:
    parts = urlsplit(match.group(0))
    return f"{parts.scheme}://{parts.netloc}/[redacted]"


class _RequestLogRedaction(logging.Filter):
    """httpx 的 INFO 记录带完整 URL，会把私密路径与查询参数写进日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.args:
            return True
        message = record.getMessage()
        redacted = _URL_PATTERN.sub(_redact_url, message)
        if redacted == message:
            return True
        record.msg = redacted
        record.args = ()
        return True


def install_request_log_redaction() -> None:
    """给 httpx 日志装一次 URL 脱敏过滤器，重复调用不会叠加。"""
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(item, _RequestLogRedaction) for item in httpx_logger.filters):
        httpx_logger.addFilter(_RequestLogRedaction())


def observe_operation(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Log duration and outcome without arguments, return values, or error text."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            started = monotonic()
            state, error_type = "succeeded", None
            try:
                return function(*args, **kwargs)
            except Exception as error:
                state, error_type = "failed", type(error).__name__
                raise
            finally:
                logger.info(
                    json.dumps(
                        {
                            "event": "operation_completed",
                            "operation": name,
                            "request_id": request_id_context.get(),
                            "state": state,
                            "elapsed_ms": round((monotonic() - started) * 1000, 3),
                            "error_type": error_type,
                        },
                        ensure_ascii=False,
                    )
                )

        return run

    return decorate
