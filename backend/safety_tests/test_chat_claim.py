"""Offline SQLite checks for chat request claims; never opens PostgreSQL."""

import asyncio
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import chat
from app.chat import ChatRequest, ChatResponse
from app.models import ChatRequestClaim, ChatTurn, User


class ChatClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'test.db'}")
        SQLModel.metadata.create_all(self.engine, tables=[User.__table__, ChatTurn.__table__, ChatRequestClaim.__table__])
        self.patcher = patch.object(chat, "engine", self.engine)
        self.patcher.start()
        self.feature = patch.object(chat.settings, "CHAT_IDEMPOTENCY_CLAIMS_ENABLED", True)
        self.feature.start()
        self.owner = uuid.uuid4()
        self.key = uuid.uuid4()

    def tearDown(self):
        self.feature.stop()
        self.patcher.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def test_parallel_claim_only_one_wins(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: chat._claim_chat_request(self.owner, self.key, "a" * 64), range(2)))
        self.assertCountEqual(results, ["claimed", "request_in_progress"])
        with self.assertRaises(HTTPException) as error:
            chat._claim_chat_request(self.owner, self.key, "b" * 64)
        self.assertEqual(error.exception.detail["code"], "idempotency_key_reused")

    def test_expiry_and_failure_never_auto_reclaim(self):
        self.assertEqual(chat._claim_chat_request(self.owner, self.key, "a" * 64), "claimed")
        with Session(self.engine) as session:
            claim = session.exec(select(ChatRequestClaim)).one()
            claim.created_at = datetime.now(UTC) - timedelta(minutes=4)
            session.add(claim)
            session.commit()
        self.assertEqual(chat._claim_chat_request(self.owner, self.key, "a" * 64), "request_outcome_unknown")
        chat._mark_claim_terminal(self.owner, self.key, "unknown")
        self.assertEqual(chat._claim_chat_request(self.owner, self.key, "a" * 64), "request_outcome_unknown")

    def test_turn_and_completion_are_committed_together(self):
        body = ChatRequest(message="hello", client_request_id=self.key)
        request_hash = chat._request_hash(body)
        self.assertEqual(chat._claim_chat_request(self.owner, self.key, request_hash), "claimed")
        response = ChatResponse(answer="answer", request_id=str(uuid.uuid4()))
        chat._persist_chat_turn(self.owner, body, request_hash, response)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(ChatRequestClaim)).one().status, "completed")
            self.assertEqual(session.exec(select(ChatTurn)).one().answer, "answer")
        self.assertEqual(chat._claim_chat_request(self.owner, self.key, request_hash), "completed")

    def test_claim_query_failure_is_persistence_error(self):
        body = ChatRequest(message="hello", client_request_id=self.key)
        response = ChatResponse(answer="answer", request_id=str(uuid.uuid4()))
        session = MagicMock()
        session.exec.side_effect = SQLAlchemyError("unavailable")
        manager = MagicMock()
        manager.__enter__.return_value = session
        with patch.object(chat, "Session", return_value=manager):
            with self.assertRaises(chat.ChatHistoryPersistenceError):
                chat._persist_chat_turn(self.owner, body, chat._request_hash(body), response)
        session.rollback.assert_called_once()
        session.add.assert_not_called()

    def test_disabled_flag_does_not_access_claim_table(self):
        ChatRequestClaim.__table__.drop(self.engine)
        body = ChatRequest(message="hello", client_request_id=self.key)

        async def generate(*_args):
            return "answer"

        request = SimpleNamespace(state=SimpleNamespace(request_id=str(uuid.uuid4())))
        user = SimpleNamespace(id=self.owner)
        with (
            patch.object(chat.settings, "CHAT_IDEMPOTENCY_CLAIMS_ENABLED", False),
            patch.object(chat, "generate_answer", side_effect=generate),
        ):
            self.assertEqual(asyncio.run(chat.create_chat(request, user, body)).answer, "answer")
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(ChatTurn)).one().answer, "answer")

    def test_concurrent_requests_call_model_once_and_replay(self):
        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()
            calls = 0

            async def generate(*_args):
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()
                return "answer"

            body = ChatRequest(message="hello", client_request_id=self.key)
            user = SimpleNamespace(id=self.owner)
            def request():
                return SimpleNamespace(state=SimpleNamespace(request_id=str(uuid.uuid4())))
            with patch.object(chat, "generate_answer", side_effect=generate):
                first = asyncio.create_task(chat.create_chat(request(), user, body))
                await asyncio.wait_for(started.wait(), timeout=5)
                with self.assertRaises(HTTPException) as error:
                    await chat.create_chat(request(), user, body)
                self.assertEqual(error.exception.detail["code"], "request_in_progress")
                release.set()
                self.assertEqual((await first).answer, "answer")
                self.assertEqual((await chat.create_chat(request(), user, body)).answer, "answer")
            self.assertEqual(calls, 1)

        asyncio.run(scenario())

    def test_cancelled_model_call_marks_result_unknown(self):
        async def scenario():
            started = asyncio.Event()

            async def generate(*_args):
                started.set()
                await asyncio.Event().wait()

            body = ChatRequest(message="hello", client_request_id=self.key)
            user = SimpleNamespace(id=self.owner)
            request = SimpleNamespace(state=SimpleNamespace(request_id=str(uuid.uuid4())))
            with patch.object(chat, "generate_answer", side_effect=generate):
                task = asyncio.create_task(chat.create_chat(request, user, body))
                await asyncio.wait_for(started.wait(), timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(
                chat._claim_chat_request(self.owner, self.key, chat._request_hash(body)),
                "request_outcome_unknown",
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
