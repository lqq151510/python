"""Check citation labels only; label validity is not semantic entailment."""

import re

from app.grounded_answer import INSUFFICIENT_EVIDENCE_ANSWER
from app.knowledge import KnowledgeSource

_MARKER = re.compile(r"\[S([0-9]+)\]")
_SUSPICIOUS = re.compile(
    r"(?:[\[【［][ \t]*(?:[SsＳｓ][ \t]*(?:[-+]?[0-9０-９][^\]】］\r\n]*|"
    r"[A-Za-z](?=[\]】］]|\r?$))|(?:source|来源)[ \t]*[-+]?[0-9０-９]"
    r"[^\]】］\r\n]*)|(?<![A-Za-z0-9_])[\[【［][ \t]*[0-9０-９]"
    r"[^\]】］\r\n]*)(?:[\]】］]|(?=\r?$))",
    re.MULTILINE | re.IGNORECASE,
)
_FENCED_CODE = re.compile(r"(?ms)^[ \t]*```[^\n]*\n.*?^[ \t]*```")
_INLINE_CODE = re.compile(r"(?<!`)`[^`\n]*`(?!`)")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
_NEUTRAL_HEADING = re.compile(r"^#{1,6}\s*(?:回答|答案|结论|说明|摘要|总结)\s*$")
_PLACEHOLDER = re.compile(r"^(?:答案|结论正文|答案正文|回答内容|答案内容)$")
_MAX_EXCERPT = 300
_MAX_SOURCES = 3


def render_document_answer(
    answer: str, sources: list[KnowledgeSource], *, complete: bool = True
) -> str:
    """Return labelled text, or clearly identified verbatim source excerpts.

    This only verifies that label numbers exist and cover the displayed blocks;
    it does not establish whether a source semantically supports any claim.
    """
    text = answer.strip()
    if complete and text == INSUFFICIENT_EVIDENCE_ANSWER:
        return text

    available = {source.citation for source in sources}
    if len(available) != len(sources) or any(
        re.fullmatch(r"S[1-9][0-9]*", item) is None for item in available
    ):
        return "来源编号无效，无法安全展示回答；请重新检索后重试。"
    # Code literals cannot authenticate claims or provide citation markers.
    prose = _INLINE_CODE.sub("【行内代码】", _FENCED_CODE.sub("【代码片段】", text))
    markers = _MARKER.findall(prose)
    candidates = _SUSPICIOUS.findall(prose)
    has_unknown = any(f"S{number}" not in available for number in markers)
    # Reject malformed labels such as [s1] / [S 1], even alongside valid ones.
    malformed = any(_MARKER.fullmatch(item) is None for item in candidates)
    if (
        complete
        and markers
        and not has_unknown
        and not malformed
        and not _only_placeholder(prose)
        and _code_blocks_labelled(text)
        and _all_blocks_labelled(prose)
    ):
        return text

    parts = [
        "模型回答未能可靠标注来源；以下为检索到的原文摘录，请核对后使用。"
        "来源编号仅用于定位原文，不代表已验证答案的语义正确性。"
    ]
    for source in sources[:_MAX_SOURCES]:
        excerpt = source.content[:_MAX_EXCERPT]
        if len(source.content) > _MAX_EXCERPT:
            excerpt += "…（原文已截断）"
        parts.append(f"[{source.citation}] 原文摘录：\n{excerpt}")
    return "\n\n".join(parts)


def _only_placeholder(text: str) -> bool:
    """Reject known template-only output; this does not verify answer meaning."""
    without_heading = "\n".join(
        line for line in text.splitlines() if not _NEUTRAL_HEADING.fullmatch(line.strip())
    )
    content = _MARKER.sub("", without_heading).strip()
    content = re.sub(r"^(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)、][ \t]*)", "", content)
    content = content.strip(" \t\r\n。.!！:：*_")
    return _PLACEHOLDER.fullmatch(content) is not None


def _code_blocks_labelled(text: str) -> bool:
    """Each fenced block needs its own citation immediately after the fence."""
    for block in _FENCED_CODE.finditer(text):
        following = text[block.end() :]
        if re.match(r"^[ \t]*(?:\r?\n[ \t]*)?\[S[1-9][0-9]*\]", following) is None:
            return False
    return True


def _all_blocks_labelled(text: str) -> bool:
    # Code literals have already been removed; a placeholder keeps their block.
    if "```" in text:  # Unclosed / malformed fence.
        return False
    for paragraph in re.split(r"\n\s*\n", text):
        current = ""
        for line in paragraph.splitlines():
            if _NEUTRAL_HEADING.fullmatch(line.strip()):
                continue
            if _BULLET.match(line) and current.strip():
                if _MARKER.search(current) is None:
                    return False
                current = ""
            current += f"\n{line}"
        if current.strip() and _MARKER.search(current) is None:
            return False
    return True
