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
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes.chat import _request_hash, create_chat
from app.chat import ChatRequest
from app.knowledge import (
    KnowledgeError,
    KnowledgeSource,
    SearchResponse,
    clear_corpus_cache,
    search_result,
)
from app.models import Document, DocumentChunk, User
from app.semantic import SemanticUnavailable


class DocumentScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[User.__table__, Document.__table__, DocumentChunk.__table__],
        )
        self.owner = uuid.uuid4()
        self.other = uuid.uuid4()
        self.documents = [uuid.uuid4() for _ in range(21)]
        with Session(self.engine) as session:
            for idx, identifier in enumerate(self.documents):
                session.add(
                    Document(
                        id=identifier,
                        owner_id=self.owner if idx < 20 else self.other,
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
        self.statements: list[str] = []
        event.listen(
            self.engine,
            "before_cursor_execute",
            lambda _conn, _cursor, statement, *_: self.statements.append(statement),
        )

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
        for invalid in (self.documents[-1], uuid.uuid4()):
            with self.subTest(invalid=invalid), Session(self.engine) as session:
                self.statements.clear()
                with self.assertRaises(KnowledgeError) as error:
                    search_result(
                        session,
                        self.owner,
                        "common",
                        document_ids=[self.documents[0], invalid],
                    )
                self.assertEqual(error.exception.status_code, 404)
                self.assertEqual(error.exception.code, "document_not_found")
                self.assertEqual(len(self.statements), 1)

    def test_cold_search_loads_ranking_text_then_hit_rows(self) -> None:
        with Session(self.engine) as session:
            result = search_result(
                session, self.owner, "common", document_ids=self.documents[:-1]
            )

        self.assertEqual(len(result.sources), 5)
        # 归属、有序 ID、排序文本、命中行。
        self.assertEqual(len(self.statements), 4)
        content_sql = [sql for sql in self.statements if "content" in sql.lower()]
        self.assertEqual(len(content_sql), 2, self.statements)
        self.assertLessEqual(
            min(sql.count("?") for sql in content_sql),
            len(result.sources) + 2,
        )

    def test_cold_search_matches_a_cleared_cache_search(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:-1]
        with Session(self.engine) as session:
            first = search_result(session, self.owner, "common", document_ids=scope)
        clear_corpus_cache()
        with Session(self.engine) as session:
            second = search_result(session, self.owner, "common", document_ids=scope)
        self.assertEqual(
            [(source.chunk_id, source.score) for source in first.sources],
            [(source.chunk_id, source.score) for source in second.sources],
        )

    def test_repeat_bm25_loads_only_hit_rows(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:-1]
        with Session(self.engine) as session:
            first = search_result(session, self.owner, "common", document_ids=scope)
            self.statements.clear()
            second = search_result(session, self.owner, "scoped", document_ids=scope)
        self.assertEqual(len(second.sources), 5)
        self.assertTrue(all("scoped" in source.content for source in second.sources))
        content_sql = [sql for sql in self.statements if "content" in sql.lower()]
        self.assertEqual(len(content_sql), 1, self.statements)
        # 命中行才带正文参数；全量查询会为范围内每份文档都绑定 ID。
        self.assertLessEqual(
            content_sql[0].count("?"),
            len(second.sources) + 2,
            content_sql[0],
        )
        clear_corpus_cache()
        with Session(self.engine) as session:
            fresh = search_result(session, self.owner, "scoped", document_ids=scope)
        self.assertEqual(second.model_dump(), fresh.model_dump())
        self.assertEqual(len(first.sources), 5)

    def test_repeat_vector_loads_only_hit_rows(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:-1]
        seen: list[list[str]] = []

        def rank(*args, **_kwargs):
            seen.append(list(args[2]))
            return [(0, 0.9)]

        with (
            patch("app.semantic.vector_rank", side_effect=rank),
            Session(self.engine) as session,
        ):
            first = search_result(
                session, self.owner, "common", document_ids=scope, method="vector"
            )
            self.statements.clear()
            second = search_result(
                session, self.owner, "scoped", document_ids=scope, method="vector"
            )
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(
            [source.model_dump() for source in first.sources],
            [source.model_dump() for source in second.sources],
        )
        content_sql = [sql for sql in self.statements if "content" in sql.lower()]
        self.assertEqual(len(content_sql), 1, self.statements)
        self.assertLessEqual(
            content_sql[0].count("?"), len(second.sources) + 2, content_sql[0]
        )

    def test_vector_after_keyword_reuses_snapshot(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:2]
        seen: list[list[str]] = []

        def rank(*args, **_kwargs):
            seen.append(list(args[2]))
            return [(1, 0.8)]

        with (
            patch("app.semantic.vector_rank", side_effect=rank),
            Session(self.engine) as session,
        ):
            search_result(session, self.owner, "common", document_ids=scope)
            self.statements.clear()
            result = search_result(
                session, self.owner, "common", document_ids=scope, method="vector"
            )
        self.assertEqual(len(seen), 1)
        self.assertIn("common scoped evidence", seen[0][1])
        content_sql = [sql for sql in self.statements if "content" in sql.lower()]
        self.assertEqual(len(content_sql), 1, self.statements)
        self.assertLessEqual(content_sql[0].count("?"), 3, content_sql[0])
        self.assertEqual(result.sources[0].score, 0.8)

    def test_added_chunk_invalidates_vector_snapshot(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:2]
        seen: list[list[str]] = []

        def rank(*args, **_kwargs):
            texts = list(args[2])
            seen.append(texts)
            match = next(
                (index for index, text in enumerate(texts) if "zebrafish" in text),
                0,
            )
            return [(match, 0.7)]

        with (
            patch("app.semantic.vector_rank", side_effect=rank),
            Session(self.engine) as session,
        ):
            search_result(
                session, self.owner, "common", document_ids=scope, method="vector"
            )
            session.add(
                DocumentChunk(
                    document_id=scope[0],
                    position=1,
                    heading="added",
                    line_start=2,
                    line_end=2,
                    content="unique zebrafish token",
                )
            )
            session.commit()
            result = search_result(
                session, self.owner, "zebrafish", document_ids=scope, method="vector"
            )
        self.assertTrue(any("zebrafish" in text for text in seen[1]))
        self.assertEqual(result.sources[0].content, "unique zebrafish token")

    def test_cached_hybrid_timeout_falls_back_without_full_reread(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:-1]
        calls = 0

        def rank(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [(0, 0.91)]
            raise SemanticUnavailable("vector_timeout")

        with (
            patch("app.semantic.vector_rank", side_effect=rank),
            Session(self.engine) as session,
        ):
            search_result(
                session, self.owner, "common", document_ids=scope, method="hybrid"
            )
            self.statements.clear()
            second = search_result(
                session, self.owner, "common", document_ids=scope, method="hybrid"
            )
        self.assertEqual(second.method, "bm25")
        self.assertEqual(second.warning, "vector_timeout")
        content_sql = [sql for sql in self.statements if "content" in sql.lower()]
        self.assertEqual(len(content_sql), 1, self.statements)
        self.assertLessEqual(
            content_sql[0].count("?"), len(second.sources) + 2, content_sql[0]
        )
        clear_corpus_cache()
        with Session(self.engine) as session:
            fresh = search_result(
                session, self.owner, "common", document_ids=scope, method="bm25"
            )
        self.assertEqual(
            [(source.chunk_id, source.score) for source in second.sources],
            [(source.chunk_id, source.score) for source in fresh.sources],
        )

    def test_added_chunk_invalidates_cached_ranking(self) -> None:
        clear_corpus_cache()
        scope = self.documents[:2]
        with Session(self.engine) as session:
            search_result(session, self.owner, "common", document_ids=scope)
            session.add(
                DocumentChunk(
                    document_id=scope[0],
                    position=1,
                    heading="added",
                    line_start=2,
                    line_end=2,
                    content="unique zebrafish token",
                )
            )
            session.commit()
            result = search_result(session, self.owner, "zebrafish", document_ids=scope)
        self.assertEqual(result.sources[0].content, "unique zebrafish token")

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
