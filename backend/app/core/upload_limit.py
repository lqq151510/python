from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.knowledge import MAX_FILE_BYTES

MAX_UPLOAD_BODY_BYTES = MAX_FILE_BYTES + 64 * 1024
MAX_CODE_UPLOAD_BODY_BYTES = 5 * 1024 * 1024
UPLOAD_ERROR = {
    "code": "upload_body_too_large",
    "message": "上传请求过大，请使用不超过 256 KiB 的单个文档。",
}
CODE_UPLOAD_ERROR = {
    "code": "code_upload_body_too_large",
    "message": "代码附件总大小不能超过 5 MiB。",
}


class DocumentUploadLimitMiddleware:
    """Bound the multipart body before the parser can spool an arbitrary file."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        if path == f"{settings.API_V1_STR}/documents":
            max_body_bytes, error = MAX_UPLOAD_BODY_BYTES, UPLOAD_ERROR
        elif path == f"{settings.API_V1_STR}/code/workspace/files":
            max_body_bytes, error = MAX_CODE_UPLOAD_BODY_BYTES, CODE_UPLOAD_ERROR
        else:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse(status_code=400, content={"detail": "请求长度不合法。"})(
                scope, receive, send
            )
            return
        if length > max_body_bytes:
            await JSONResponse(status_code=413, content={"detail": error})(
                scope, receive, send
            )
            return
        consumed = 0

        async def bounded_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > max_body_bytes:
                    raise HTTPException(413, detail=error)
            return message

        await self.app(scope, bounded_receive, send)
