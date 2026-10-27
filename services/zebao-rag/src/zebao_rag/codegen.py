"""Bounded, local code generation through the configured Ollama model."""
from __future__ import annotations

import re

import httpx

from .config import CODE_MAX_TOKENS, CODE_MODEL, CODE_NUM_CTX, CODE_TIMEOUT_S, OLLAMA_BASE_URL

CODE_SYSTEM_PROMPT = """你是泽宝的本地编程助手，擅长 Python、AI 应用和 RAG 工程。

根据用户需求输出可读、尽量可运行的代码。遵循用户指定的语言和技术栈；未指定时优先 Python 3.12。
回答包含必要的代码和简短使用说明。信息不足时先说明采用的假设。
不要声称代码已经运行或通过测试。不要输出内部推理过程。你没有文件、命令行或网络工具，只能在回复中提供内容。"""


class CodeGenerationError(RuntimeError):
    """A user-facing error from the local code generation service."""


def _final_content(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, str):
        return ""

    # Some runtimes include thinking tags in content instead of a separate field.
    if "</think>" in content:
        content = content.rsplit("</think>", maxsplit=1)[-1]
    elif "<think>" in content:
        content = content.split("<think>", maxsplit=1)[0]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    return content.strip()


def generate_code(task: str) -> str:
    """Generate code locally; the model receives no RAG or filesystem tools."""
    task = task.strip()
    if not task:
        raise CodeGenerationError("请描述你要生成的代码。")
    if len(task) > 4000:
        raise CodeGenerationError("需求最多 4000 个字符，请拆成更小的任务。")

    payload = {
        "model": CODE_MODEL,
        "messages": [
            {"role": "system", "content": CODE_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ],
        "stream": False,
        "options": {
            "num_ctx": CODE_NUM_CTX,
            "num_predict": CODE_MAX_TOKENS,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
        },
    }

    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=CODE_TIMEOUT_S,
        )
    except httpx.ConnectError as exc:
        raise CodeGenerationError("无法连接本机 Ollama，请先启动 Ollama 服务。") from exc
    except httpx.TimeoutException as exc:
        raise CodeGenerationError("模型生成超时，请缩小需求后重试。") from exc

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = f"HTTP {response.status_code}"
        try:
            error = response.json().get("error")
            if isinstance(error, str) and error.strip():
                detail = error.strip()[:240]
        except (ValueError, AttributeError):
            pass
        raise CodeGenerationError(f"Ollama 请求失败：{detail}") from exc

    try:
        answer = _final_content(response.json().get("message"))
    except (ValueError, AttributeError) as exc:
        raise CodeGenerationError("Ollama 返回了无法解析的响应。") from exc
    if not answer:
        raise CodeGenerationError("模型没有返回代码内容，请换一种方式描述需求。")
    return answer
