"""Small, owner-scoped lexical retrieval; no external embedding API is used."""

import hashlib
import math
import re
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.models import Document, DocumentChunk

MAX_FILE_BYTES = 256 * 1024
MAX_DOCUMENT_CHARACTERS = 120_000
MAX_DOCUMENTS_PER_USER = 20
MAX_CHUNKS_PER_DOCUMENT = 256
RetrievalMethod = Literal["bm25", "vector", "hybrid"]
CHUNK_CHARACTERS = 1000
CHUNK_OVERLAP = 150
# A moderate length penalty keeps precise evidence in long chunks competitive.
BM25_K1 = 1.5
BM25_B = 0.5
EXACT_PHRASE_BOOST = 2.0


class KnowledgeError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code, self.code, self.message = status_code, code, message
        super().__init__(code)


class DocumentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    size_bytes: int
    chunk_count: int
    created_at: datetime


class DocumentsPublic(BaseModel):
    data: list[DocumentPublic]
    count: int


class DocumentChunkPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    position: int
    heading: str
    line_start: int
    line_end: int
    content: str


class DocumentDetail(BaseModel):
    document: DocumentPublic
    chunks: list[DocumentChunkPublic]


class DocumentUploadResponse(BaseModel):
    document: DocumentPublic
    duplicate: bool


class KnowledgeSource(BaseModel):
    citation: str
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    filename: str
    heading: str
    line_start: int
    line_end: int
    content: str
    score: float


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=5)
    document_id: uuid.UUID | None = None
    method: RetrievalMethod = "bm25"


class SearchResponse(BaseModel):
    query: str
    method: RetrievalMethod = "bm25"
    sources: list[KnowledgeSource]
    document_id: uuid.UUID | None = None
    warning: str | None = None
    cache_status: str | None = None
    vector_store: str | None = None


@dataclass(frozen=True)
class TextChunk:
    heading: str
    line_start: int
    line_end: int
    content: str


def parse_document(filename: str, raw: bytes) -> tuple[str, str, str]:
    # Metadata only: never use the supplied filename as a filesystem path.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise KnowledgeError(422, "invalid_filename", "文件名不符合要求。")
    if not name.lower().endswith((".md", ".txt")):
        raise KnowledgeError(
            415, "unsupported_file", "请选择 Markdown（.md）或 TXT 文件。"
        )
    if len(raw) > MAX_FILE_BYTES:
        raise KnowledgeError(413, "file_too_large", "单个文件不能超过 256 KiB。")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise KnowledgeError(
            422, "invalid_encoding", "文件需要使用 UTF-8 编码。"
        ) from None
    if not text.strip() or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise KnowledgeError(422, "invalid_text", "文件为空或包含无法处理的控制字符。")
    if len(text) > MAX_DOCUMENT_CHARACTERS:
        raise KnowledgeError(413, "document_too_long", "文档不能超过 120000 个字符。")
    return name, text, hashlib.sha256(text.encode()).hexdigest()


def split_document(text: str) -> list[TextChunk]:
    lines = text.splitlines(keepends=True)
    sections: list[tuple[int, int, str]] = []
    start, heading = 0, "正文"
    fence: str | None = None
    for index, line in enumerate(lines):
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match[1][0]
            fence = marker if fence is None else (None if marker == fence else fence)
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if match:
            if index > start:
                sections.append((start, index, heading))
            start, heading = index, match[1][:255]
    sections.append((start, len(lines), heading))
    chunks: list[TextChunk] = []
    for first, last, section_heading in sections:
        content = "".join(lines[first:last])
        for offset in range(0, len(content), CHUNK_CHARACTERS - CHUNK_OVERLAP):
            end = min(offset + CHUNK_CHARACTERS, len(content))
            fragment = content[offset:end]
            if fragment.strip():
                chunks.append(
                    TextChunk(
                        heading=section_heading,
                        line_start=first + content[:offset].count("\n") + 1,
                        line_end=first
                        + content[: max(offset, end - 1)].count("\n")
                        + 1,
                        content=fragment.strip(),
                    )
                )
            if end == len(content):
                break
    if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
        raise KnowledgeError(
            413, "too_many_chunks", "文档结构过于零碎，请拆成较小的文件。"
        )
    return chunks


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold()):
        if "\u4e00" <= word[0] <= "\u9fff" and len(word) > 1:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


# 分词是这条链上最贵的步骤（5,000 段约 0.5 s），而片段的文本一旦写入就不再变化。
# 按「这一批片段 ID」缓存分词结果：新增或删除文档都会换 key，旧条目自然过期。
_CORPUS_CACHE_SIZE = 4
_corpus_cache: OrderedDict[str, tuple[list[Counter[str]], list[str]]] = OrderedDict()


def corpus_key(chunk_ids: list[uuid.UUID]) -> str:
    digest = hashlib.blake2b(digest_size=16)
    for chunk_id in chunk_ids:
        digest.update(chunk_id.bytes)
    return f"{len(chunk_ids)}:{digest.hexdigest()}"


def tokenized_corpus(
    key: str, contents: list[str]
) -> tuple[list[Counter[str]], list[str]]:
    """返回 (每段的词频, 每段归一化文本)；同一快照只分词一次。"""
    cached = _corpus_cache.get(key)
    if cached is not None:
        _corpus_cache.move_to_end(key)
        return cached
    counts = [Counter(tokenize(content)) for content in contents]
    normalized = [" ".join(content.casefold().split()) for content in contents]
    value = (counts, normalized)
    _corpus_cache[key] = value
    _corpus_cache.move_to_end(key)
    while len(_corpus_cache) > _CORPUS_CACHE_SIZE:
        _corpus_cache.popitem(last=False)
    return value


def clear_corpus_cache() -> None:
    _corpus_cache.clear()


def rank_chunks(
    query: str,
    contents: list[str],
    limit: int = 5,
    key: str | None = None,
) -> list[tuple[int, float]]:
    terms = set(tokenize(query)[:256])
    if not terms or not contents:
        return []
    normalized_query = " ".join(query.casefold().split())
    counts: list[Counter[str]]
    normalized: list[str]
    if key is None:
        counts = [Counter(tokenize(content)) for content in contents]
        normalized = [" ".join(content.casefold().split()) for content in contents]
    else:
        counts, normalized = tokenized_corpus(key, contents)
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / len(lengths) or 1
    frequencies = {term: sum(term in count for count in counts) for term in terms}
    results: list[tuple[int, float]] = []
    for index, count in enumerate(counts):
        score = 0.0
        for term in terms:
            frequency = count[term]
            if not frequency:
                continue
            inverse = math.log(
                1 + (len(counts) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            score += (
                inverse
                * frequency
                * (BM25_K1 + 1)
                / (
                    frequency
                    + BM25_K1 * (1 - BM25_B + BM25_B * lengths[index] / average)
                )
            )
        if score > 0:
            if len(terms) > 1 and normalized_query in normalized[index]:
                score *= EXACT_PHRASE_BOOST
            results.append((index, score))
    return sorted(results, key=lambda result: (-result[1], result[0]))[:limit]


def search_documents(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int = 5,
    document_id: uuid.UUID | None = None,
) -> list[KnowledgeSource]:
    return search_result(session, owner_id, query, limit, document_id).sources


def search_result(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int = 5,
    document_id: uuid.UUID | None = None,
    method: RetrievalMethod = "bm25",
    vector_store: str | None = None,
) -> SearchResponse:
    from app.core.config import settings
    from app.semantic import SemanticUnavailable, fuse_rankings, vector_rank

    if document_id is not None:
        owned_document(session, owner_id, document_id)
    statement = (
        select(DocumentChunk, Document)
        .join(Document, col(DocumentChunk.document_id) == col(Document.id))
        .where(Document.owner_id == owner_id)
        .order_by(
            col(Document.created_at), col(Document.id), col(DocumentChunk.position)
        )
    )
    if document_id is not None:
        statement = statement.where(Document.id == document_id)
    rows = session.exec(statement).all()
    texts = [f"{doc.filename}\n{chunk.heading}\n{chunk.content}" for chunk, doc in rows]
    depth = 20 if method == "hybrid" else limit
    ranked = (
        rank_chunks(query, texts, depth, corpus_key([chunk.id for chunk, _ in rows]))
        if method != "vector"
        else []
    )
    actual_method, warning = method, None
    cache_info: dict[str, str] = {}
    if rows and method != "bm25":
        try:
            dense = vector_rank(
                query,
                [str(chunk.id) for chunk, _ in rows],
                texts,
                depth,
                str(owner_id),
                vector_store,
                cache_info,
            )
            ranked = (
                fuse_rankings(ranked, dense, limit=limit)
                if method == "hybrid"
                else dense
            )
        except SemanticUnavailable as exc:
            if method == "vector":
                raise KnowledgeError(
                    503,
                    exc.code,
                    "语义检索暂不可用，请切换关键词检索或检查本地检索服务。",
                ) from None
            actual_method, warning = "bm25", exc.code
    ranked = ranked[:limit]
    sources: list[KnowledgeSource] = []
    for number, (index, score) in enumerate(ranked, 1):
        chunk, doc = rows[index]
        sources.append(
            KnowledgeSource(
                citation=f"S{number}",
                document_id=doc.id,
                chunk_id=chunk.id,
                filename=doc.filename,
                heading=chunk.heading,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                content=chunk.content,
                score=round(score, 6),
            )
        )
    return SearchResponse(
        query=query,
        method=actual_method,
        sources=sources,
        document_id=document_id,
        warning=warning,
        cache_status=cache_info.get("status"),
        vector_store=(vector_store or settings.RAG_VECTOR_STORE)
        if method != "bm25"
        else None,
    )


def owned_document(
    session: Session, owner_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = session.exec(
        select(Document).where(
            Document.id == document_id, Document.owner_id == owner_id
        )
    ).first()
    if document is None:
        raise KnowledgeError(404, "document_not_found", "文档不存在或已删除。")
    return document


def retrieve_context(
    owner_id: uuid.UUID, query: str, document_id: uuid.UUID | None = None
) -> list[KnowledgeSource]:
    # Called in the thread pool: its DB session is not shared with the async route.
    from app.core.db import engine

    with Session(engine) as session:
        return search_documents(session, owner_id, query, document_id=document_id)


def retrieve_context_result(
    owner_id: uuid.UUID,
    query: str,
    document_id: uuid.UUID | None = None,
    method: RetrievalMethod = "bm25",
) -> SearchResponse:
    from app.core.db import engine

    with Session(engine) as session:
        return search_result(
            session, owner_id, query, document_id=document_id, method=method
        )


def format_context(sources: list[KnowledgeSource]) -> str:
    return "\n\n".join(
        f"[{source.citation}] 文件={source.filename}; 标题={source.heading}; "
        f"行={source.line_start}-{source.line_end}\n{source.content}"
        for source in sources
    )
