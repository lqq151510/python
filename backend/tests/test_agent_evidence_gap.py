import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import web_agent
from app.grounded_answer import INSUFFICIENT_EVIDENCE_ANSWER
from app.web_agent import (
    AgentSessionPublic,
    AgentTurnPublic,
    AgentTurnRequest,
    AgentTurnResponse,
    Evidence,
    HistoryTurn,
)


def _saved_response(
    body: AgentTurnRequest,
    request_id: str,
    answer: str,
    steps: list[web_agent.AgentStepPublic],
    stopped: bool,
) -> AgentTurnResponse:
    now = datetime.now(UTC)
    return AgentTurnResponse(
        session=AgentSessionPublic(
            id=uuid4(),
            title=body.message[:40],
            turn_count=1,
            created_at=now,
            updated_at=now,
        ),
        turn=AgentTurnPublic(
            id=uuid4(),
            message=body.message,
            answer=answer,
            steps=steps,
            stopped=stopped,
            created_at=now,
        ),
        request_id=request_id,
    )


def _patch_turn(
    monkeypatch: pytest.MonkeyPatch,
    evidence: Evidence,
    history: list[HistoryTurn] | None = None,
) -> dict[str, object]:
    saved: dict[str, object] = {}

    def prepare(
        owner_id: object, session_id: object, message: str
    ) -> tuple[list[HistoryTurn], Evidence]:
        del owner_id, session_id, message
        return list(history or []), evidence

    def save_turn(
        owner_id: object,
        body: AgentTurnRequest,
        request_id: str,
        answer: str,
        steps: list[web_agent.AgentStepPublic],
        stopped: bool,
    ) -> AgentTurnResponse:
        del owner_id
        saved["answer"] = answer
        saved["steps"] = steps
        return _saved_response(body, request_id, answer, steps, stopped)

    def reject_client(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("no model client should be constructed")

    monkeypatch.setattr(web_agent, "_prepare", prepare)
    monkeypatch.setattr(web_agent, "_save_turn", save_turn)
    monkeypatch.setattr(web_agent, "AsyncOpenAI", reject_client)
    return saved


def _run(message: str) -> AgentTurnResponse:
    return asyncio.run(
        web_agent.run_turn(uuid4(), AgentTurnRequest(message=message), "agent-gap")
    )


def test_literal_gap_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = _patch_turn(
        monkeypatch,
        Evidence("检索预览", False, False, ("状态检查使用 GET /healthz。",)),
    )
    response = _run("请给出 Redis 的验证命令。")
    assert response.turn.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert saved["answer"] == INSUFFICIENT_EVIDENCE_ANSWER
    steps = saved["steps"]
    assert isinstance(steps, list)
    assert steps[0].name == "search_knowledge"
    assert steps[0].detail == "检索预览"


def test_matching_command_still_calls_model(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def model_loop(
        history: list[HistoryTurn],
        question: str,
        evidence: str,
        owner_id: object,
        request_id: str,
    ) -> tuple[str, list[web_agent.AgentStepPublic], bool]:
        del history, evidence, owner_id, request_id
        called.append(question)
        return "运行 redis-cli ping。", [], False

    saved = _patch_turn(
        monkeypatch,
        Evidence("检索预览", False, False, ("运行 `redis-cli ping`。",)),
    )
    monkeypatch.setattr(web_agent, "_model_loop", model_loop)
    response = _run("请给出 Redis 的验证命令。")
    assert called == ["请给出 Redis 的验证命令。"]
    assert response.turn.answer == "运行 redis-cli ping。"
    assert saved["answer"] == "运行 redis-cli ping。"


def test_history_does_not_fill_literal_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = _patch_turn(
        monkeypatch,
        Evidence("检索预览", False, False, ("状态检查使用 GET /healthz。",)),
        [HistoryTurn("上一轮问了什么？", "之前只提到健康检查。")],
    )
    response = _run("请给出 Redis 的验证命令。")
    assert response.turn.answer == INSUFFICIENT_EVIDENCE_ANSWER
    assert saved["answer"] == INSUFFICIENT_EVIDENCE_ANSWER


def test_initial_retrieval_keeps_five_full_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [
        SimpleNamespace(
            heading=f"标题{index}",
            content=f"正文{index}" + ("很长" * 200),
            filename="notes.md",
            line_start=index,
            line_end=index,
        )
        for index in range(1, 7)
    ]
    seen: dict[str, int] = {}

    def retrieve(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(sources=sources)

    def search_text(
        session: object, owner_id: object, query: str, limit: int
    ) -> list[object]:
        del session, owner_id, query
        seen["limit"] = limit
        return []

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(self, *args: object) -> bool:
            del args
            return False

    monkeypatch.setattr(web_agent, "retrieve_context_result", retrieve)
    monkeypatch.setattr(web_agent, "search_text", search_text)
    monkeypatch.setattr(web_agent, "Session", lambda engine: DummySession())
    evidence = web_agent.collect_evidence(uuid4(), "问题")
    full_tail = "很长" * 200
    assert seen["limit"] == 5
    assert evidence.chunks == tuple(
        f"标题{index}\n正文{index}{full_tail}" for index in range(1, 6)
    )
    assert full_tail not in evidence.text
    assert full_tail in evidence.chunks[0]
