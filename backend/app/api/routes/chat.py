import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, func, select
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, SessionDep
from app.chat import (
    ChatHistoryDetail,
    ChatHistoryResponse,
    ChatHistorySummary,
    ChatRequest,
    ChatResponse,
    ChatServiceError,
    generate_answer,
)
from app.core.db import engine
from app.knowledge import KnowledgeError, retrieve_context_result
from app.models import ChatTurn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse, response_model_exclude_defaults=True)
async def create_chat(
    request: Request, current_user: CurrentUser, body: ChatRequest
) -> ChatResponse:
    """Ask a question and atomically persist the successful turn for its owner."""
    request_id = request.state.request_id
    request_hash = _request_hash(body)
    if body.client_request_id is not None:
        existing = await run_in_threadpool(
            _find_existing_turn, current_user.id, body.client_request_id
        )
        if existing:
            _ensure_request_matches(existing, request_hash)
            return _response_from_turn(existing, request_id)

    sources = []
    retrieval = None
    if body.use_documents:
        try:
            retrieval = await run_in_threadpool(
                retrieve_context_result,
                current_user.id,
                body.message,
                body.document_id,
                body.retrieval_method,
            )
            sources = retrieval.sources
        except KnowledgeError as exc:
            request.state.chat_error_code = exc.code
            raise HTTPException(
                exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from None
        if not sources:
            request.state.chat_error_code = "no_document_evidence"
            raise HTTPException(
                422,
                detail={
                    "code": "no_document_evidence",
                    "message": "知识库中没有匹配的片段，请先导入相关文档或调整问题。",
                },
            )
    try:
        answer = (
            await generate_answer(body.message, request_id, sources)
            if sources
            else await generate_answer(body.message, request_id)
        )
    except ChatServiceError as exc:
        request.state.chat_error_code = exc.code
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from None

    response = ChatResponse(
        answer=answer,
        request_id=request_id,
        sources=sources,
        retrieval_method=retrieval.method if retrieval else None,
        retrieval_warning=retrieval.warning if retrieval else None,
    )
    try:
        return await run_in_threadpool(
            _persist_chat_turn, current_user.id, body, request_hash, response
        )
    except ChatHistoryPersistenceError:
        request.state.chat_error_code = "chat_history_persistence_failed"
        raise HTTPException(
            status_code=503,
            detail={
                "code": "chat_history_persistence_failed",
                "message": "回答已生成，但历史记录暂时无法保存，请稍后重试。",
            },
        ) from None


class ChatHistoryPersistenceError(Exception):
    pass


def _request_hash(body: ChatRequest) -> str:
    payload = {
        "message": body.message,
        "use_documents": body.use_documents,
        "document_id": str(body.document_id) if body.document_id else None,
        "retrieval_method": body.retrieval_method,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_request_matches(turn: ChatTurn, request_hash: str) -> None:
    if turn.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_key_reused",
                "message": "请求编号已用于其他内容，请为新请求生成新的编号。",
            },
        )


def _sources_from_turn(turn: ChatTurn) -> list[dict[str, object]]:
    try:
        sources = json.loads(turn.sources_json)
    except TypeError, json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail={"code": "chat_history_corrupt", "message": "历史记录无法读取。"},
        ) from None
    if not isinstance(sources, list):
        raise HTTPException(
            status_code=500,
            detail={"code": "chat_history_corrupt", "message": "历史记录无法读取。"},
        )
    return sources


def _response_from_turn(turn: ChatTurn, request_id: str) -> ChatResponse:
    return ChatResponse(
        answer=turn.answer,
        request_id=request_id,
        sources=_sources_from_turn(turn),
        retrieval_method=turn.retrieval_method,
        retrieval_warning=turn.retrieval_warning,
    )


def _find_existing_turn(
    owner_id: uuid.UUID, client_request_id: uuid.UUID
) -> ChatTurn | None:
    with Session(engine) as session:
        return session.exec(
            select(ChatTurn).where(
                ChatTurn.owner_id == owner_id,
                ChatTurn.client_request_id == client_request_id,
            )
        ).first()


def _persist_chat_turn(
    owner_id: uuid.UUID,
    body: ChatRequest,
    request_hash: str,
    response: ChatResponse,
) -> ChatResponse:
    with Session(engine) as session:
        turn = ChatTurn(
            owner_id=owner_id,
            client_request_id=body.client_request_id,
            request_hash=request_hash,
            request_id=response.request_id,
            message=body.message,
            answer=response.answer,
            sources_json=json.dumps(
                [source.model_dump(mode="json") for source in response.sources],
                ensure_ascii=False,
            ),
            retrieval_method=response.retrieval_method,
            retrieval_warning=response.retrieval_warning,
        )
        session.add(turn)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            if body.client_request_id is not None:
                existing = session.exec(
                    select(ChatTurn).where(
                        ChatTurn.owner_id == owner_id,
                        ChatTurn.client_request_id == body.client_request_id,
                    )
                ).first()
                if existing:
                    _ensure_request_matches(existing, request_hash)
                    return _response_from_turn(existing, response.request_id)
            logger.error(
                "chat_history_persistence_failed request_id=%s error_type=%s",
                response.request_id,
                type(exc).__name__,
            )
            raise ChatHistoryPersistenceError from None
    return response


@router.get("/history", response_model=ChatHistoryResponse)
def read_chat_history(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
) -> ChatHistoryResponse:
    turns = session.exec(
        select(ChatTurn)
        .where(ChatTurn.owner_id == current_user.id)
        .order_by(col(ChatTurn.created_at).desc(), col(ChatTurn.id).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    count = session.exec(
        select(func.count())
        .select_from(ChatTurn)
        .where(ChatTurn.owner_id == current_user.id)
    ).one()
    data = [
        ChatHistorySummary(
            id=turn.id,
            message=turn.message,
            answer_preview=turn.answer[:240],
            source_count=len(_sources_from_turn(turn)),
            retrieval_method=turn.retrieval_method,
            created_at=turn.created_at,
        )
        for turn in turns
    ]
    return ChatHistoryResponse(data=data, count=count)


@router.get("/history/{turn_id}", response_model=ChatHistoryDetail)
def read_chat_history_detail(
    turn_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> ChatHistoryDetail:
    turn = session.exec(
        select(ChatTurn).where(
            ChatTurn.id == turn_id, ChatTurn.owner_id == current_user.id
        )
    ).first()
    if not turn:
        raise HTTPException(404, detail="历史记录不存在。")
    return ChatHistoryDetail(
        id=turn.id,
        message=turn.message,
        answer=turn.answer,
        request_id=turn.request_id,
        sources=_sources_from_turn(turn),
        retrieval_method=turn.retrieval_method,
        retrieval_warning=turn.retrieval_warning,
        created_at=turn.created_at,
    )
