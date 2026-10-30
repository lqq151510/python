"""Offline proxy handling checks: no network or application database."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from app import chat, code_agent, web_agent
from app.model_http import is_loopback_model_url

INVALID_PROXY_ENV = {
    "ALL_PROXY": "socks5://127.0.0.1:9",
    "HTTP_PROXY": "socks5://127.0.0.1:9",
    "HTTPS_PROXY": "socks5://127.0.0.1:9",
    "NO_PROXY": "",
}


class ModelProxyTests(unittest.TestCase):
    def test_only_explicit_loopback_hosts_match(self):
        for url in ("http://localhost:11434/v1", "http://127.0.0.1/v1", "http://[::1]:1/v1"):
            self.assertTrue(is_loopback_model_url(url))
        for url in ("https://localhost.example/v1", "http://127.0.0.1.example/v1", "http://host.docker.internal/v1", "http://[broken"):
            self.assertFalse(is_loopback_model_url(url))

    def test_chat_and_web_agent_close_loopback_client_and_remote_uses_default(self):
        async def run(module, url):
            seen = []

            def factory(**kwargs):
                seen.append(kwargs.get("http_client"))
                # Remote use of None preserves the real OpenAI default; avoid
                # constructing it under the intentionally invalid proxy.
                if kwargs.get("http_client") is None:
                    kwargs["http_client"] = httpx.AsyncClient(trust_env=False)
                return AsyncOpenAI(**kwargs)

            with patch.object(module.settings, "DEEPSEEK_BASE_URL", url), patch.object(
                module.settings, "DEEPSEEK_API_KEY", SecretStr("test-only")
            ), patch.object(module, "AsyncOpenAI", factory):
                if module is chat:
                    with patch.object(chat, "_request_answer", AsyncMock(return_value="ok")):
                        self.assertEqual(await chat.generate_answer("test", "id"), "ok")
                else:
                    reply = web_agent.ModelReply(content="ok", tool_calls=[])
                    with patch.object(web_agent, "_complete", AsyncMock(return_value=reply)):
                        self.assertEqual((await web_agent._model_loop([], "test", "test", uuid4(), "id"))[0], "ok")
            if seen[0] is not None:
                self.assertFalse(seen[0].trust_env)
                self.assertTrue(seen[0].is_closed)
            return seen[0]

        with patch.dict("os.environ", INVALID_PROXY_ENV):
            for module in (chat, web_agent):
                self.assertIsNotNone(asyncio.run(run(module, "http://[::1]:11434/v1")))
                self.assertIsNone(asyncio.run(run(module, "https://remote.invalid/v1")))

    def test_code_agent_loopback_and_remote_proxy_selection(self):
        async def run_real_local():
            captured = []

            async def post(client, *_args, **_kwargs):
                captured.append(client)
                return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

            with patch.object(code_agent, "_model_settings", return_value=("test", SecretStr("test"), "http://127.0.0.1:11434/v1")), patch.object(
                code_agent.settings, "CODE_API_FORMAT", "openai_chat_completions"
            ), patch.object(httpx.AsyncClient, "post", post):
                self.assertEqual(await code_agent._request_model("system", "test", "id"), "ok")
            self.assertFalse(captured[0].trust_env)
            self.assertTrue(captured[0].is_closed)

        async def run(base_url):
            captured = []
            real_client = httpx.AsyncClient

            def factory(**kwargs):
                captured.append(kwargs["trust_env"])
                # A MockTransport client avoids network and deliberately
                # bypasses the invalid SOCKS proxy for this isolated test.
                kwargs["trust_env"] = False
                kwargs["transport"] = httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
                )
                return real_client(**kwargs)

            with patch.object(code_agent, "_model_settings", return_value=("test", SecretStr("test"), base_url)), patch.object(
                code_agent.settings, "CODE_API_FORMAT", "openai_chat_completions"
            ), patch.object(code_agent.httpx, "AsyncClient", side_effect=factory):
                self.assertEqual(await code_agent._request_model("system", "test", "id"), "ok")
            return captured

        with patch.dict("os.environ", INVALID_PROXY_ENV):
            asyncio.run(run_real_local())
            self.assertEqual(asyncio.run(run("http://127.0.0.1:11434/v1")), [False])
            self.assertEqual(asyncio.run(run("https://remote.invalid/v1")), [True])


if __name__ == "__main__":
    unittest.main()
