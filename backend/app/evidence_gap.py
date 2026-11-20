"""Refuse a document answer only when a named literal is absent.

Related chunks stay available. Uncertain questions return None so the model
path is unchanged. These patterns are fixed; holdout misses do not add rules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_COMMAND_DEMAND = re.compile(r"给出.{0,16}命令|哪条.{0,16}命令|验证命令|删除命令")
_CONFIG_KEY_DEMAND = re.compile(r"哪个环境变量|哪些环境变量|什么环境变量|配置键")
_CONFIG_FILE_DEMAND = re.compile(r"哪个配置文件|哪些配置文件|什么配置文件")
_URL_DEMAND = re.compile(r"完整.{0,16}URL|接口.{0,8}URL", re.IGNORECASE)
_PROPER_TERMS = ("毫秒", "抖动", "HPA", "副本")
_TOP_CHUNKS = 5

_COMMAND_SHAPE = re.compile(
    r"`[^`\n]*\s[^`\n]*`"
    r"|(?:redis-cli|kubectl|alembic|curl|docker|python3?|bash|sh|uv|bun)\s+\S+"
    r"|\b(?:GET|POST|PUT|DELETE|PATCH)\s+/[A-Za-z0-9]",
    re.IGNORECASE,
)
_CONFIG_NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_FILENAME = re.compile(
    r"(?<![\w.-])[\w.-]+\.(?:md|txt|env|ya?ml|toml|json|ini|conf|py|sh)(?![\w.-])",
    re.IGNORECASE,
)
_URL_OR_PATH = re.compile(r"https?://|/(?:api|healthz|v1|docs)\b", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
# 这些词只说明“要哪一类字面”，不能用来把片段收窄到某个对象。
_GENERIC_IDENTIFIERS = frozenset(
    {
        "url",
        "http",
        "https",
        "api",
        "json",
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "html",
        "css",
    }
)


def missing_required_literal(question: str, chunks: Sequence[str]) -> str | None:
    """Return a reason code when the relevant chunks lack a demanded literal.

    A named identifier such as ``Redis`` or ``pool_wait`` keeps only chunks
    that contain it. A command or config name in some other chunk does not
    satisfy that demand. Questions without such an identifier still use the
    top chunks together.
    """
    text = _supporting_text(question, chunks)
    if _COMMAND_DEMAND.search(question) and _COMMAND_SHAPE.search(text) is None:
        return "missing_command"
    if _CONFIG_KEY_DEMAND.search(question) and _CONFIG_NAME.search(text) is None:
        return "missing_config_key"
    if _CONFIG_FILE_DEMAND.search(question) and not _has_file_or_url(text):
        return "missing_config_file"
    if _URL_DEMAND.search(question) and not _has_file_or_url(text):
        return "missing_url"
    if any(term in question and term not in text for term in _PROPER_TERMS):
        return "missing_term"
    return None


def _has_file_or_url(text: str) -> bool:
    return _FILENAME.search(text) is not None or _URL_OR_PATH.search(text) is not None


def _supporting_text(question: str, chunks: Sequence[str]) -> str:
    top = list(chunks[:_TOP_CHUNKS])
    names = _distinctive_identifiers(question)
    if names:
        folded = [name.casefold() for name in names]
        top = [
            chunk
            for chunk in top
            if any(name in chunk.casefold() for name in folded)
        ]
    return "\n".join(top)


def _distinctive_identifiers(question: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER.finditer(question):
        token = match.group(0)
        key = token.casefold()
        if key in _GENERIC_IDENTIFIERS or key in seen:
            continue
        seen.add(key)
        found.append(token)
    return tuple(found)
