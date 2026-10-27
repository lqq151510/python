from fastapi import APIRouter

from app.api.routes import (
    agent,
    aigc,
    chat,
    code,
    documents,
    images,
    items,
    learning,
    login,
    notes,
    private,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(chat.router)
api_router.include_router(code.router)
api_router.include_router(documents.router)
api_router.include_router(images.router)
api_router.include_router(agent.router)
api_router.include_router(learning.router)
api_router.include_router(notes.router)
api_router.include_router(aigc.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
