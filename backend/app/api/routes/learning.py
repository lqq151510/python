"""Small hands-on journal and reproducible vector-store comparisons."""

import uuid
from time import monotonic
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from redis import RedisError
from sqlalchemy import text
from sqlmodel import select

from app import knowledge, learning_store, retrieval_cache
from app.api.deps import CurrentUser, SessionDep
from app.knowledge import KnowledgeError
from app.models import Document

router = APIRouter(prefix="/learning", tags=["learning"])


class NoteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=2000)


class CompareRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    document_id: uuid.UUID | None = None


def unavailable() -> HTTPException:
    return HTTPException(
        503,
        detail={
            "code": "learning_store_unavailable",
            "message": "学习记录库暂不可用，请检查本地 MySQL 及学习表初始化。",
        },
    )


@router.get("")
def list_records(current_user: CurrentUser) -> dict[str, Any]:
    try:
        return {"data": learning_store.listing(current_user.id)}
    except Exception:
        raise unavailable() from None


@router.post("", status_code=201)
def create_record(current_user: CurrentUser, body: NoteRequest) -> dict[str, Any]:
    try:
        return learning_store.add(current_user.id, body.title, body.notes)
    except Exception:
        raise unavailable() from None


@router.delete("/{record_id}")
def delete_record(current_user: CurrentUser, record_id: uuid.UUID) -> dict[str, Any]:
    try:
        removed = learning_store.remove(current_user.id, record_id)
    except Exception:
        raise unavailable() from None
    if not removed:
        raise HTTPException(404, detail="学习记录不存在。")
    return {"message": "学习记录已删除。"}


@router.get("/cache/stats")
def cache_stats(current_user: CurrentUser) -> dict[str, Any]:
    try:
        return {"available": True, **retrieval_cache.stats(str(current_user.id))}
    except RedisError, ValueError:
        return {"available": False, "hit": 0, "miss": 0}


@router.post("/compare")
def compare_stores(
    session: SessionDep, current_user: CurrentUser, body: CompareRequest
) -> dict[str, Any]:
    if (
        session.exec(
            select(Document.id).where(Document.owner_id == current_user.id).limit(1)
        ).first()
        is None
    ):
        raise HTTPException(
            422,
            detail={
                "code": "no_documents",
                "message": "先上传一份短文档，再开始检索对照实验。",
            },
        )
    # Validate scope before accessing either vector database or writing a record.
    if body.document_id is not None:
        try:
            knowledge.owned_document(session, current_user.id, body.document_id)
        except KnowledgeError as exc:
            raise HTTPException(
                exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from None
    # Ensure the result can be saved before starting expensive inference.
    try:
        with learning_store.engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise unavailable() from None
    comparisons: dict[str, dict[str, Any]] = {}
    for store in ("milvus", "chroma"):
        start = monotonic()
        try:
            result = knowledge.search_result(
                session,
                current_user.id,
                body.query,
                document_id=body.document_id,
                method="vector",
                vector_store=store,
            )
        except KnowledgeError as exc:
            raise HTTPException(
                exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from None
        comparisons[store] = {
            "elapsed_ms": round((monotonic() - start) * 1000),
            "cache_status": result.cache_status,
            "hits": [
                {
                    "chunk_id": str(source.chunk_id),
                    "filename": source.filename,
                    "score": source.score,
                }
                for source in result.sources
            ],
        }
    first = {hit["chunk_id"] for hit in comparisons["milvus"]["hits"]}
    second = {hit["chunk_id"] for hit in comparisons["chroma"]["hits"]}
    report = {
        "query": body.query,
        "stores": comparisons,
        "shared_hits": len(first & second),
        "candidate_note": "相同模型和资料；耗时包含当前缓存状态，不能据此判断数据库性能优劣。",
    }
    try:
        return learning_store.add(
            current_user.id,
            "向量检索对照实验",
            body.query,
            kind="comparison",
            result=report,
        )
    except Exception:
        raise unavailable() from None
