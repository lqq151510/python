"""Verify document-only Ollama residency settings with no real network or DB."""

import asyncio
import json
import os
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


def source() -> KnowledgeSource:
    return KnowledgeSource(
        citation="S1",
        document_id=uuid4(),
        chunk_id=uuid4(),
        filename="synthetic.md",
        heading="sample",
        line_start=1,
        line_end=1,
        content="缓存保留17分钟。",
        score=1.0,
    )


class DocumentKeepAliveTests(unittest.TestCase):
    def test_config_default_and_bounds(self):
        values = {
            "_env_file": None,
            "SECRET_KEY": "unit-test-only-secret",
            "PROJECT_NAME": "unit-test-only",
            "DATABASE_URL": "postgresql://unit:unit@127.0.0.1:1/unit",
            "FIRST_SUPERUSER": "unit@example.com",
            "FIRST_SUPERUSER_PASSWORD": "unit-test-only-password",
        }
        self.assertIsNone(Settings(**values).CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS)
        self.assertEqual(
            Settings(**values, CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS=0)
            .CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS,
            0,
        )
        for raw, expected in (("", None), ("0", 0), ("60", 60)):
            with (
                self.subTest(raw=raw),
                patch.dict(
                    os.environ,
                    {"CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS": raw},
                    clear=True,
                ),
            ):
                self.assertEqual(
                    Settings(**values).CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS,
                    expected,
                )
        for value in (-1, 86401):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(**values, CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS=value)

    def test_only_local_opted_in_document_request_sends_keep_alive(self):
        async def request(base_url, keep_alive, with_sources, strict):
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
                base_url=base_url,
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            ) as client:
                with (
                    patch.object(chat.settings, "DEEPSEEK_BASE_URL", base_url),
                    patch.object(
                        chat.settings,
                        "CHAT_DOCUMENT_OLLAMA_KEEP_ALIVE_SECONDS",
                        keep_alive,
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
                        [source()] if with_sources else None,
                    )
            self.assertEqual(len(sent), 1)
            return sent[0]

        cases = (
            ("http://127.0.0.1:11434/v1", None, True, False, None),
            ("http://127.0.0.1:11434/v1", 0, True, False, 0),
            ("http://localhost:11434/v1", 60, True, False, 60),
            ("http://[::1]:11434/v1", 0, True, False, 0),
            ("https://unit.invalid/v1", 0, True, False, None),
            ("http://localhost.example.com/v1", 0, True, False, None),
            ("http://127.0.0.1:11434/v1", 0, False, False, None),
            ("http://127.0.0.1:11434/v1", 0, True, True, None),
        )
        for base_url, keep_alive, with_sources, strict, expected in cases:
            with self.subTest(base_url=base_url, keep_alive=keep_alive, strict=strict):
                body = asyncio.run(request(base_url, keep_alive, with_sources, strict))
                self.assertEqual(body["thinking"], {"type": "disabled"})
                if expected is None:
                    self.assertNotIn("keep_alive", body)
                else:
                    self.assertEqual(body["keep_alive"], expected)


if __name__ == "__main__":
    unittest.main()
