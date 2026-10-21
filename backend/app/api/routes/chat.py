from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser
from app.chat import ChatRequest, ChatResponse, ChatServiceError, generate_answer
from app.knowledge import KnowledgeError, retrieve_context_result

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse, response_model_exclude_defaults=True)
async def create_chat(
    request: Request, current_user: CurrentUser, body: ChatRequest
) -> ChatResponse:
    """Ask a single question; authentication follows the existing user dependency."""
    request_id = request.state.request_id
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
    return ChatResponse(
        answer=answer,
        request_id=request_id,
        sources=sources,
        retrieval_method=retrieval.method if retrieval else None,
        retrieval_warning=retrieval.warning if retrieval else None,
    )
