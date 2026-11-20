"""Small, owner-scoped lexical retrieval; no external embedding API is used."""

import hashlib
import math
import re
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

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


@dataclass(frozen=True)
class _TokenizedCorpus:
    counts: list[Counter[str]]
    normalized: list[str]
    length_penalties: list[float]
    document_frequencies: Counter[str]


# 片段写入后不再变化，新增或删除文档会生成新的快照键。
_CORPUS_CACHE_SIZE = 4
_corpus_cache: OrderedDict[str, _TokenizedCorpus] = OrderedDict()
# 检索范围 -> (分词快照键, 有序片段 ID, 向量检索用的原文)。
# 纯向量检索没有分词快照，第一项为 None。
_ScopeSnapshot = tuple[str | None, tuple[uuid.UUID, ...], tuple[str, ...]]
_scope_cache: OrderedDict[str, _ScopeSnapshot] = OrderedDict()
_corpus_cache_lock = Lock()


def corpus_key(chunk_ids: list[uuid.UUID]) -> str:
    digest = hashlib.blake2b(digest_size=16)
    for chunk_id in chunk_ids:
        digest.update(chunk_id.bytes)
    return f"{len(chunk_ids)}:{digest.hexdigest()}"


def _build_tokenized_corpus(contents: list[str]) -> _TokenizedCorpus:
    counts: list[Counter[str]] = []
    normalized: list[str] = []
    lengths: list[int] = []
    document_frequencies: Counter[str] = Counter()
    for content in contents:
        tokens = tokenize(content)
        count = Counter(tokens)
        counts.append(count)
        normalized.append(" ".join(content.casefold().split()))
        lengths.append(len(tokens))
        document_frequencies.update(count.keys())
    average = (sum(lengths) / len(lengths) if lengths else 0) or 1
    length_penalties = [
        BM25_K1 * (1 - BM25_B + BM25_B * length / average) for length in lengths
    ]
    return _TokenizedCorpus(counts, normalized, length_penalties, document_frequencies)


def tokenized_corpus(key: str, contents: list[str]) -> _TokenizedCorpus:
    with _corpus_cache_lock:
        cached = _corpus_cache.get(key)
        if cached is not None:
            _corpus_cache.move_to_end(key)
            return cached

    # 分词在锁外执行，避免阻塞其他语料的缓存命中。
    value = _build_tokenized_corpus(contents)
    with _corpus_cache_lock:
        cached = _corpus_cache.setdefault(key, value)
        _corpus_cache.move_to_end(key)
        if len(_corpus_cache) > _CORPUS_CACHE_SIZE:
            _corpus_cache.popitem(last=False)
    return cached


def clear_corpus_cache() -> None:
    with _corpus_cache_lock:
        _corpus_cache.clear()
        _scope_cache.clear()


def _cached_corpus(key: str) -> _TokenizedCorpus | None:
    with _corpus_cache_lock:
        cached = _corpus_cache.get(key)
        if cached is not None:
            _corpus_cache.move_to_end(key)
        return cached


def _scope_snapshot(scope_key: str) -> _ScopeSnapshot | None:
    with _corpus_cache_lock:
        cached = _scope_cache.get(scope_key)
        if cached is not None:
            _scope_cache.move_to_end(scope_key)
        return cached


def _remember_scope(
    scope_key: str,
    corpus_key: str | None,
    chunk_ids: tuple[uuid.UUID, ...],
    texts: tuple[str, ...],
) -> None:
    with _corpus_cache_lock:
        _scope_cache[scope_key] = (corpus_key, chunk_ids, texts)
        _scope_cache.move_to_end(scope_key)
        if len(_scope_cache) > _CORPUS_CACHE_SIZE:
            _scope_cache.popitem(last=False)


def _score_corpus(
    terms: set[str], query: str, corpus: _TokenizedCorpus, limit: int
) -> list[tuple[int, float]]:
    if not terms or not corpus.counts:
        return []
    chunk_count = len(corpus.counts)
    inverse_frequencies: dict[str, float] = {}
    for term in terms:
        document_frequency = corpus.document_frequencies[term]
        if document_frequency:
            inverse_frequencies[term] = math.log(
                1
                + (chunk_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
    if not inverse_frequencies:
        return []
    normalized_query = " ".join(query.casefold().split())
    results: list[tuple[int, float]] = []
    for index, count in enumerate(corpus.counts):
        score = _bm25_score(count, corpus, index, inverse_frequencies)
        if score <= 0:
            continue
        if len(terms) > 1 and normalized_query in corpus.normalized[index]:
            score *= EXACT_PHRASE_BOOST
        results.append((index, score))
    return sorted(results, key=lambda result: (-result[1], result[0]))[:limit]


def _bm25_score(
    count: Counter[str],
    corpus: _TokenizedCorpus,
    index: int,
    inverse_frequencies: dict[str, float],
) -> float:
    score = 0.0
    length_penalty = corpus.length_penalties[index]
    for term, inverse in inverse_frequencies.items():
        frequency = count[term]
        if frequency:
            score += inverse * frequency * (BM25_K1 + 1) / (frequency + length_penalty)
    return score


def _rank_corpus(
    query: str, corpus: _TokenizedCorpus, limit: int
) -> list[tuple[int, float]]:
    return _score_corpus(set(tokenize(query)[:256]), query, corpus, limit)


def rank_chunks(
    query: str,
    contents: list[str],
    limit: int = 5,
    key: str | None = None,
) -> list[tuple[int, float]]:
    terms = set(tokenize(query)[:256])
    if not terms or not contents:
        return []
    corpus = (
        _build_tokenized_corpus(contents)
        if key is None
        else tokenized_corpus(key, contents)
    )
    return _score_corpus(terms, query, corpus, limit)


def _retrieval_scope_key(
    owner_id: uuid.UUID,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
) -> str:
    if document_ids is not None:
        scope = ",".join(sorted(str(item) for item in document_ids))
    elif document_id is not None:
        scope = str(document_id)
    else:
        scope = "*"
    return f"{owner_id}|{scope}"


def _scoped_chunks(statement, owner_id, document_id, document_ids):
    """片段顺序必须固定，授权 ID 和排序文本的下标才能对齐。"""
    statement = statement.join(
        Document, col(DocumentChunk.document_id) == col(Document.id)
    ).where(Document.owner_id == owner_id)
    if document_id is not None:
        statement = statement.where(Document.id == document_id)
    if document_ids is not None:
        statement = statement.where(col(Document.id).in_(document_ids))
    return statement.order_by(
        col(Document.created_at), col(Document.id), col(DocumentChunk.position)
    )


def _chunk_id_statement(
    owner_id: uuid.UUID,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
) -> SelectOfScalar[uuid.UUID]:
    """只取有序片段 ID。排序必须与排序文本查询一致，缓存下标才能对上。"""
    return _scoped_chunks(
        select(DocumentChunk.id), owner_id, document_id, document_ids
    )


def authorized_chunk_ids(
    session: Session,
    owner_id: uuid.UUID,
    document_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
) -> tuple[uuid.UUID, ...]:
    """校验文档属于当前用户，并返回该范围内的有序片段 ID。"""
    if document_id is not None:
        owned_document(session, owner_id, document_id)
    if document_ids is not None:
        if not document_ids or document_id is not None:
            raise ValueError("invalid document scope")
        owned_ids = session.exec(
            select(Document.id).where(
                Document.owner_id == owner_id, col(Document.id).in_(document_ids)
            )
        ).all()
        if set(owned_ids) != set(document_ids):
            raise KnowledgeError(404, "document_not_found", "文档不存在或已删除。")
    return tuple(
        session.exec(_chunk_id_statement(owner_id, document_id, document_ids)).all()
    )


def _load_ranking_snapshot(
    session: Session,
    owner_id: uuid.UUID,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
) -> tuple[tuple[uuid.UUID, ...], tuple[str, ...]]:
    """读取排序用的文件名、标题和正文，不装配整行 ORM。"""
    rows = session.exec(
        _scoped_chunks(
            select(
                DocumentChunk.id,
                Document.filename,
                DocumentChunk.heading,
                DocumentChunk.content,
            ),
            owner_id,
            document_id,
            document_ids,
        )
    ).all()
    chunk_ids: list[uuid.UUID] = []
    texts: list[str] = []
    for chunk_id, filename, heading, content in rows:
        chunk_ids.append(chunk_id)
        texts.append(f"{filename}\n{heading}\n{content}")
    return tuple(chunk_ids), tuple(texts)


def _public_sources(
    pairs: list[tuple[DocumentChunk, Document]], scores: list[float]
) -> list[KnowledgeSource]:
    sources: list[KnowledgeSource] = []
    for number, ((chunk, doc), score) in enumerate(zip(pairs, scores, strict=True), 1):
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
    return sources


def _sources_for_ranked_ids(
    session: Session,
    owner_id: uuid.UUID,
    chunk_ids: tuple[uuid.UUID, ...],
    ranked: list[tuple[int, float]],
) -> list[KnowledgeSource] | None:
    if not ranked:
        return []
    wanted = [chunk_ids[index] for index, _score in ranked]
    rows = session.exec(
        select(DocumentChunk, Document)
        .join(Document, col(DocumentChunk.document_id) == col(Document.id))
        .where(
            col(Document.owner_id) == owner_id,
            col(DocumentChunk.id).in_(wanted),
        )
    ).all()
    by_id = {chunk.id: (chunk, doc) for chunk, doc in rows}
    if len(by_id) != len(wanted):
        return None
    pairs = [by_id[chunk_id] for chunk_id in wanted]
    return _public_sources(pairs, [score for _index, score in ranked])


def _cached_bm25_sources(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
    current_ids: tuple[uuid.UUID, ...],
) -> list[KnowledgeSource] | None:
    snapshot = _scope_snapshot(
        _retrieval_scope_key(owner_id, document_id, document_ids)
    )
    if snapshot is None:
        return None
    corpus_key_value, expected_ids, texts = snapshot
    if corpus_key_value is None or len(texts) != len(expected_ids):
        return None
    corpus = _cached_corpus(corpus_key_value)
    if corpus is None or len(corpus.counts) != len(expected_ids):
        return None
    if current_ids != expected_ids:
        return None
    ranked = _rank_corpus(query, corpus, limit)
    return _sources_for_ranked_ids(session, owner_id, expected_ids, ranked)


def _apply_dense(
    method: RetrievalMethod,
    query: str,
    identifiers: list[str],
    texts: list[str],
    depth: int,
    owner_id: uuid.UUID,
    vector_store: str | None,
    lexical: list[tuple[int, float]],
    limit: int,
) -> tuple[list[tuple[int, float]], RetrievalMethod, str | None, str | None]:
    ranked = lexical
    actual_method: RetrievalMethod = method
    warning = None
    cache_status = None
    if identifiers and method != "bm25":
        from app.semantic import SemanticUnavailable, fuse_rankings, vector_rank

        cache_info: dict[str, str] = {}
        try:
            dense = vector_rank(
                query,
                identifiers,
                texts,
                depth,
                str(owner_id),
                vector_store,
                cache_info,
            )
            ranked = (
                fuse_rankings(lexical, dense, limit=limit)
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
        cache_status = cache_info.get("status")
    return ranked[:limit], actual_method, warning, cache_status


def _search_response(
    query: str,
    method: RetrievalMethod,
    sources: list[KnowledgeSource],
    document_id: uuid.UUID | None,
    warning: str | None,
    cache_status: str | None,
    vector_store: str | None,
    requested_method: RetrievalMethod,
) -> SearchResponse:
    from app.core.config import settings

    return SearchResponse(
        query=query,
        method=method,
        sources=sources,
        document_id=document_id,
        warning=warning,
        cache_status=cache_status,
        vector_store=(vector_store or settings.RAG_VECTOR_STORE)
        if requested_method != "bm25"
        else None,
    )


def _cached_semantic_response(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
    method: Literal["vector", "hybrid"],
    vector_store: str | None,
    current_ids: tuple[uuid.UUID, ...],
) -> SearchResponse | None:
    snapshot = _scope_snapshot(
        _retrieval_scope_key(owner_id, document_id, document_ids)
    )
    if snapshot is None:
        return None
    corpus_key_value, expected_ids, texts = snapshot
    if len(texts) != len(expected_ids) or not texts:
        return None
    corpus = None
    if method == "hybrid":
        if corpus_key_value is None:
            return None
        corpus = _cached_corpus(corpus_key_value)
        if corpus is None or len(corpus.counts) != len(expected_ids):
            return None
    if current_ids != expected_ids:
        return None
    depth = 20 if method == "hybrid" else limit
    lexical = _rank_corpus(query, corpus, depth) if corpus is not None else []
    ranked, actual_method, warning, cache_status = _apply_dense(
        method,
        query,
        [str(chunk_id) for chunk_id in expected_ids],
        list(texts),
        depth,
        owner_id,
        vector_store,
        lexical,
        limit,
    )
    sources = _sources_for_ranked_ids(session, owner_id, expected_ids, ranked)
    if sources is None:
        return None
    return _search_response(
        query,
        actual_method,
        sources,
        document_id,
        warning,
        cache_status,
        vector_store,
        method,
    )


def search_documents(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int = 5,
    document_id: uuid.UUID | None = None,
) -> list[KnowledgeSource]:
    return search_result(session, owner_id, query, limit, document_id).sources


def _search_uncached(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int,
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
    method: RetrievalMethod,
    vector_store: str | None,
) -> SearchResponse:
    chunk_ids, texts = _load_ranking_snapshot(
        session, owner_id, document_id, document_ids
    )
    depth = 20 if method == "hybrid" else limit
    text_list = list(texts)
    snapshot_key = corpus_key(list(chunk_ids)) if chunk_ids else None
    if snapshot_key is not None:
        stored_key = None
        if method != "vector":
            tokenized_corpus(snapshot_key, text_list)
            stored_key = snapshot_key
        _remember_scope(
            _retrieval_scope_key(owner_id, document_id, document_ids),
            stored_key,
            chunk_ids,
            texts,
        )
    lexical = (
        rank_chunks(query, text_list, depth, snapshot_key)
        if method != "vector"
        else []
    )
    ranked, actual_method, warning, cache_status = _apply_dense(
        method,
        query,
        [str(chunk_id) for chunk_id in chunk_ids],
        text_list,
        depth,
        owner_id,
        vector_store,
        lexical,
        limit,
    )
    sources = _sources_for_ranked_ids(session, owner_id, chunk_ids, ranked)
    return _search_response(
        query,
        actual_method,
        [] if sources is None else sources,
        document_id,
        warning,
        cache_status,
        vector_store,
        method,
    )


def search_result(
    session: Session,
    owner_id: uuid.UUID,
    query: str,
    limit: int = 5,
    document_id: uuid.UUID | None = None,
    method: RetrievalMethod = "bm25",
    vector_store: str | None = None,
    document_ids: list[uuid.UUID] | None = None,
) -> SearchResponse:
    current_ids = authorized_chunk_ids(
        session, owner_id, document_id, document_ids
    )
    if method == "bm25":
        cached_sources = _cached_bm25_sources(
            session,
            owner_id,
            query,
            limit,
            document_id,
            document_ids,
            current_ids,
        )
        if cached_sources is not None:
            return _search_response(
                query,
                "bm25",
                cached_sources,
                document_id,
                None,
                None,
                None,
                "bm25",
            )
    if method != "bm25":
        cached_response = _cached_semantic_response(
            session,
            owner_id,
            query,
            limit,
            document_id,
            document_ids,
            method,
            vector_store,
            current_ids,
        )
        if cached_response is not None:
            return cached_response
    return _search_uncached(
        session,
        owner_id,
        query,
        limit,
        document_id,
        document_ids,
        method,
        vector_store,
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
    document_ids: list[uuid.UUID] | None = None,
) -> SearchResponse:
    from app.core.db import engine

    with Session(engine) as session:
        return search_result(
            session,
            owner_id,
            query,
            document_id=document_id,
            method=method,
            document_ids=document_ids,
        )


def format_context(sources: list[KnowledgeSource]) -> str:
    return "\n\n".join(
        f"[{source.citation}] 文件={source.filename}; 标题={source.heading}; "
        f"行={source.line_start}-{source.line_end}\n{source.content}"
        for source in sources
    )
