import uuid

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import CurrentUser
from app.chat import ChatServiceError
from app.web_agent import (
    AgentError,
    AgentSessionDetail,
    AgentSessionsPublic,
    AgentTurnRequest,
    AgentTurnResponse,
    list_sessions,
    read_session,
    run_turn,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def _agent_error(exc: AgentError) -> HTTPException:
    return HTTPException(
        exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


def _model_error(exc: ChatServiceError) -> HTTPException:
    return HTTPException(
        exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


@router.get("/sessions", response_model=AgentSessionsPublic)
def read_agent_sessions(current_user: CurrentUser) -> AgentSessionsPublic:
    return list_sessions(current_user.id)


@router.get("/sessions/{session_id}", response_model=AgentSessionDetail)
def read_agent_session(
    current_user: CurrentUser, session_id: uuid.UUID
) -> AgentSessionDetail:
    try:
        return read_session(current_user.id, session_id)
    except AgentError as exc:
        raise _agent_error(exc) from None


@router.post("/turns", response_model=AgentTurnResponse)
async def create_agent_turn(
    request: Request, current_user: CurrentUser, body: AgentTurnRequest
) -> AgentTurnResponse:
    request_id = request.state.request_id
    try:
        return await run_turn(current_user.id, body, request_id)
    except AgentError as exc:
        request.state.chat_error_code = exc.code
        raise _agent_error(exc) from None
    except ChatServiceError as exc:
        request.state.chat_error_code = exc.code
        raise _model_error(exc) from None
