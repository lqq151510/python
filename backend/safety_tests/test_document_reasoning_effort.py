"""Check optional document reasoning settings without external services or a DB."""

import asyncio
import json
import unittest
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
from pydantic import ValidationError

from app import chat
from app.core.config import Settings
from app.knowledge import KnowledgeSource


def synthetic_source() -> KnowledgeSource:
    return KnowledgeSource(
        citation="S1",
        document_id=uuid4(),
        chunk_id=uuid4(),
        filename="synthetic.md",
        heading="demo",
        line_start=1,
        line_end=1,
        content="缓存保留17分钟。",
        score=1.0,
    )


class DocumentReasoningEffortTests(unittest.TestCase):
    def test_config_validates_optional_effort(self):
        values = {
            "_env_file": None,
            "SECRET_KEY": "unit-test-only-secret",
            "PROJECT_NAME": "unit-test-only",
            "DATABASE_URL": "postgresql://unit:unit@127.0.0.1:1/unit",
            "FIRST_SUPERUSER": "unit@example.com",
            "FIRST_SUPERUSER_PASSWORD": "unit-test-only-password",
        }
        self.assertIsNone(Settings(**values).CHAT_DOCUMENT_REASONING_EFFORT)
        for blank in ("", "  "):
            with self.subTest(blank=blank):
                self.assertIsNone(
                    Settings(**values, CHAT_DOCUMENT_MODEL=blank).CHAT_DOCUMENT_MODEL
                )
        self.assertEqual(
            Settings(
                **values, CHAT_DOCUMENT_MODEL="  model:latest  "
            ).CHAT_DOCUMENT_MODEL,
            "model:latest",
        )
        with self.assertRaises(ValidationError):
            Settings(**values, CHAT_DOCUMENT_MODEL="x" * 129)
        self.assertEqual(
            Settings(
                **values, CHAT_DOCUMENT_REASONING_EFFORT="none"
            ).CHAT_DOCUMENT_REASONING_EFFORT,
            "none",
        )
        with self.assertRaises(ValidationError):
            Settings(**values, CHAT_DOCUMENT_REASONING_EFFORT="unsupported")

    def test_payload_changes_only_for_opted_in_ordinary_document_qa(self):
        async def request(effort, with_sources, strict, document_model):
            sent = []

            def respond(request):
                sent.append(json.loads(request.content))
                content = (
                    '{"answerable":false,"answer":"","evidence":[]}'
                    if strict and with_sources
                    else "缓存保留17分钟。[S1]"
                )
                return httpx.Response(
                    200,
                    json={
                        "id": "synthetic",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": content},
                            }
                        ],
                    },
                )

            async with AsyncOpenAI(
                api_key="test-only",
                base_url="https://unit.invalid/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ) as client:
                with (
                    patch.object(chat.settings, "DEEPSEEK_MODEL", "default-model"),
                    patch.object(chat.settings, "CHAT_DOCUMENT_MODEL", document_model),
                    patch.object(
                        chat.settings, "CHAT_DOCUMENT_REASONING_EFFORT", effort
                    ),
                    patch.object(
                        chat.settings, "CHAT_VALIDATE_DOCUMENT_EVIDENCE", strict
                    ),
                ):
                    await chat._request_answer(
                        client,
                        "缓存保留多久？",
                        monotonic() + 10,
                        "synthetic",
                        [synthetic_source()] if with_sources else None,
                    )
            self.assertEqual(len(sent), 1)
            return sent[0]

        for effort, with_sources, strict, document_model, expected, expected_model in (
            (None, True, False, None, None, "default-model"),
            ("none", True, False, "document-model", "none", "document-model"),
            ("low", True, False, "", "low", "default-model"),
            ("none", False, False, "document-model", None, "default-model"),
            ("none", True, True, "document-model", None, "document-model"),
        ):
            with self.subTest(effort=effort, with_sources=with_sources, strict=strict):
                body = asyncio.run(
                    request(effort, with_sources, strict, document_model)
                )
                self.assertEqual(body["model"], expected_model)
                if expected is None:
                    self.assertNotIn("reasoning_effort", body)
                else:
                    self.assertEqual(body["reasoning_effort"], expected)


if __name__ == "__main__":
    unittest.main()
