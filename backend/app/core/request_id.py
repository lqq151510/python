import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = logging.getLogger(__name__)


class ChatRequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.rstrip("/") != f"{settings.API_V1_STR}/chat":
            return await call_next(request)
        request_id = str(uuid4())
        request.state.request_id = request_id
        started_at = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Do not serialize or log the original exception: SDK exceptions may
            # contain provider payloads or credentials. Always preserve the ID.
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
            if request.headers.get("Origin") == settings.FRONTEND_HOST:
                response.headers["Access-Control-Allow-Origin"] = settings.FRONTEND_HOST
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
                response.headers["Vary"] = "Origin"
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "chat_request request_id=%s elapsed_ms=%s status_code=%s error_code=%s",
            request_id,
            round((monotonic() - started_at) * 1000),
            response.status_code,
            getattr(request.state, "chat_error_code", None),
        )
        return response
