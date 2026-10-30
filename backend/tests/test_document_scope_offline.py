"""Document scope regression tests; runnable directly without the pytest DB fixture."""

import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if __name__ == "__main__":
    os.environ.update(
        PROJECT_NAME="offline-test",
        SECRET_KEY="offline-test-secret-not-for-deployment",
        DATABASE_URL="postgresql://offline:offline@127.0.0.1:1/offline",
        FIRST_SUPERUSER="offline@example.com",
        FIRST_SUPERUSER_PASSWORD="offline-test-password",
    )

from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes.chat import _request_hash, create_chat
from app.chat import ChatRequest
from app.knowledge import KnowledgeError, KnowledgeSource, SearchResponse, search_result
from app.models import Document, DocumentChunk, User


class DocumentScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[User.__table__, Document.__table__, DocumentChunk.__table__],
        )
        self.owner = uuid.uuid4()
        self.other = uuid.uuid4()
        self.documents = [uuid.uuid4() for _ in range(4)]
        with Session(self.engine) as session:
            for idx, identifier in enumerate(self.documents):
                session.add(
                    Document(
                        id=identifier,
                        owner_id=self.owner if idx < 3 else self.other,
                        filename=f"{idx}.txt",
                        sha256=str(idx),
                        size_bytes=10,
                        chunk_count=1,
                    )
                )
                session.add(
                    DocumentChunk(
                        document_id=identifier,
                        position=0,
                        heading="scope",
                        line_start=1,
                        line_end=1,
                        content="common scoped evidence",
                    )
                )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_selected_documents_only_and_no_scope_expansion(self) -> None:
        with Session(self.engine) as session:
            result = search_result(
                session, self.owner, "common", document_ids=self.documents[:2]
            )
        self.assertEqual(
            {s.document_id for s in result.sources}, set(self.documents[:2])
        )

    def test_foreign_or_missing_document_rejected(self) -> None:
        for invalid in (self.documents[3], uuid.uuid4()):
            with self.subTest(invalid=invalid), Session(self.engine) as session:
                with self.assertRaises(KnowledgeError) as error:
                    search_result(
                        session,
                        self.owner,
                        "common",
                        document_ids=[self.documents[0], invalid],
                    )
                self.assertEqual(error.exception.status_code, 404)

    def test_request_validation_and_idempotency_scope(self) -> None:
        a, b, c = self.documents[:3]

        def request(ids: list[uuid.UUID]) -> ChatRequest:
            return ChatRequest(message="question", use_documents=True, document_ids=ids)

        self.assertEqual(_request_hash(request([a, b])), _request_hash(request([b, a])))
        self.assertNotEqual(
            _request_hash(request([a, b])), _request_hash(request([a, c]))
        )
        for data in (
            {"document_ids": []},
            {"document_ids": [a, a]},
            {"document_id": a, "document_ids": [b]},
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ChatRequest(message="question", use_documents=True, **data)
        with self.assertRaises(ValidationError):
            ChatRequest(message="question", document_ids=[a])


class ChatScopeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_passes_multi_document_scope_to_retrieval(self) -> None:
        owner, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        source = KnowledgeSource(
            citation="S1",
            document_id=a,
            chunk_id=uuid.uuid4(),
            filename="a.txt",
            heading="h",
            line_start=1,
            line_end=1,
            content="evidence",
            score=1.0,
        )
        retrieval = SearchResponse(query="question", method="bm25", sources=[source])
        request = SimpleNamespace(state=SimpleNamespace(request_id="request-id"))
        body = ChatRequest(message="question", use_documents=True, document_ids=[a, b])
        with (
            patch(
                "app.api.routes.chat.retrieve_context_result", return_value=retrieval
            ) as search,
            patch(
                "app.api.routes.chat.generate_answer",
                new_callable=AsyncMock,
                return_value="answer",
            ),
            patch(
                "app.api.routes.chat._persist_chat_turn",
                side_effect=lambda *args: args[-1],
            ),
        ):
            response = await create_chat(request, SimpleNamespace(id=owner), body)
        self.assertEqual(response.sources[0].document_id, a)
        search.assert_called_once_with(
            owner, "question", None, "bm25", document_ids=[a, b]
        )


if __name__ == "__main__":
    unittest.main()
