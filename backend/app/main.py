from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlmodel import Session
from starlette.middleware.cors import CORSMiddleware

from app import aigc_runner
from app.api.main import api_router
from app.core.aigc_lock import exclusive_aigc_runner
from app.core.config import settings
from app.core.db import engine
from app.core.request_id import LOCAL_DEV_FRONTEND_ORIGINS, APIRequestIDMiddleware
from app.core.upload_limit import DocumentUploadLimitMiddleware

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """先取得执行器独占权，再恢复任务；停掉线程之后才释放独占权。"""
    with ExitStack() as resources:
        if settings.AIGC_ENABLED:
            resources.enter_context(exclusive_aigc_runner(engine))
            with Session(engine) as session:
                aigc_runner.recover_interrupted_jobs(session)
            aigc_runner.ensure_runner_started()
        try:
            yield
        finally:
            aigc_runner.stop_runner()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

app.add_middleware(DocumentUploadLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted({settings.FRONTEND_HOST, *LOCAL_DEV_FRONTEND_ORIGINS}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(APIRequestIDMiddleware)

app.include_router(api_router, prefix=settings.API_V1_STR)
if FRONTEND_DIR.is_dir():
    app.frontend("/", directory=FRONTEND_DIR)
