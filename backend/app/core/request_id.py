import json
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.observability import install_request_log_redaction, request_id_context

logger = logging.getLogger(__name__)

# 本机开发时前端可能用 localhost 或 127.0.0.1，500 响应也要按这个来源回 CORS。
LOCAL_DEV_FRONTEND_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _apply_error_cors(response: Response, origin: str | None) -> None:
    if origin not in {settings.FRONTEND_HOST, *LOCAL_DEV_FRONTEND_ORIGINS}:
        return
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
    response.headers["Vary"] = "Origin"


class APIRequestIDMiddleware(BaseHTTPMiddleware):
    """Correlate all API responses and log bounded, sanitized request fields."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path.rstrip("/")
        if path != settings.API_V1_STR and not path.startswith(
            settings.API_V1_STR + "/"
        ):
            return await call_next(request)
        request_id = str(uuid4())
        request.state.request_id = request_id
        context_token = request_id_context.set(request_id)
        started_at = monotonic()
        error_type = None
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                # Provider exceptions can contain credentials or private payloads.
                error_type = type(error).__name__
                request.state.chat_error_code = "internal_error"
                response = JSONResponse(
                    status_code=500,
                    content={
                        "detail": {
                            "code": "internal_error",
                            "message": "服务暂时出现异常，请稍后重试。",
                        }
                    },
                )
                _apply_error_cors(response, request.headers.get("Origin"))
        finally:
            request_id_context.reset(context_token)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "api_request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": getattr(request.scope.get("route"), "path", "<unmatched>"),
                    "elapsed_ms": round((monotonic() - started_at) * 1000, 3),
                    "status_code": response.status_code,
                    "error_code": getattr(request.state, "chat_error_code", None),
                    "error_type": error_type,
                },
                ensure_ascii=False,
            ),
        )
        return response


# httpx 会原样记录 URL，其中的路径片段与查询参数属于私密数据。
install_request_log_redaction()
