"""Markdown 分块：优先按标题(##)切，超长段落再按字符滑窗兜底。

每个块带可追溯元数据：来源文件、标题路径、块序号。
"""
import re
from dataclasses import dataclass
from pathlib import Path

from .config import CHUNK_MAX_CHARS, CHUNK_OVERLAP, DOCS_DIR


@dataclass
class Chunk:
    text: str
    source: str          # 相对文件名，如 02-数据处理与分块.md
    heading: str         # 该块所属标题路径
    index: int           # 块在文件内的序号


def _split_by_headings(md: str) -> list[tuple[str, str]]:
    """按 ## 二级标题切成 (标题, 正文)；标题前的内容归入文件主标题。"""
    lines = md.splitlines()
    doc_title = ""
    sections: list[tuple[str, list[str]]] = []
    current = "（开头）"
    buf: list[str] = []
    for line in lines:
        h1 = re.match(r"^#\s+(.*)", line)
        h2 = re.match(r"^##\s+(.*)", line)
        if h1 and not doc_title:
            doc_title = h1.group(1).strip()
            continue
        if h2:
            sections.append((current, buf))
            current = h2.group(1).strip()
            buf = []
        else:
            buf.append(line)
    sections.append((current, buf))
    return [(f"{doc_title} > {h}", "\n".join(b).strip()) for h, b in sections]


def _window(text: str) -> list[str]:
    """对超长正文做带重叠的字符滑窗。"""
    if len(text) <= CHUNK_MAX_CHARS:
        return [text] if text else []
    pieces, start = [], 0
    step = CHUNK_MAX_CHARS - CHUNK_OVERLAP
    while start < len(text):
        pieces.append(text[start:start + CHUNK_MAX_CHARS])
        start += step
    return pieces


def load_chunks(docs_dir: Path = DOCS_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        md = path.read_text(encoding="utf-8")
        idx = 0
        for heading, body in _split_by_headings(md):
            for piece in _window(body):
                cleaned = piece.strip()
                if not cleaned:
                    continue
                chunks.append(
                    Chunk(
                        text=f"【{heading}】\n{cleaned}",
                        source=path.name,
                        heading=heading,
                        index=idx,
                    )
                )
                idx += 1
    return chunks
