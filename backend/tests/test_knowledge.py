import pytest

from app.knowledge import (
    CHUNK_CHARACTERS,
    KnowledgeError,
    parse_document,
    rank_chunks,
    split_document,
)


def test_utf8_bom_newlines_filename_and_deduplication() -> None:
    name, text, digest = parse_document(
        "../notes/排障.MD", b"\xef\xbb\xbf# Title\r\nFastAPI\r\n"
    )
    assert name == "排障.MD"
    assert text == "# Title\nFastAPI\n"
    assert digest == parse_document("other.txt", text.encode())[2]


@pytest.mark.parametrize(
    "filename,raw,status,code",
    [
        ("x.pdf", b"PDF", 415, "unsupported_file"),
        ("x.txt", b"a" * (256 * 1024 + 1), 413, "file_too_large"),
        ("x.txt", b"\xff", 422, "invalid_encoding"),
        ("x.md", b" \n ", 422, "invalid_text"),
        ("x.txt", b"hello\x00world", 422, "invalid_text"),
        ("x.txt", b"a" * 120001, 413, "document_too_long"),
        ("bad\nname.md", b"text", 422, "invalid_filename"),
    ],
)
def test_upload_boundaries(filename: str, raw: bytes, status: int, code: str) -> None:
    with pytest.raises(KnowledgeError) as info:
        parse_document(filename, raw)
    assert (info.value.status_code, info.value.code) == (status, code)


def test_chunk_headings_line_provenance_and_code_fences() -> None:
    text = "intro\n# Async\nFastAPI\n```python\n# not a heading\n```\n## Database\nPostgreSQL\n"
    chunks = split_document(text)
    assert [(c.heading, c.line_start, c.line_end) for c in chunks] == [
        ("正文", 1, 1),
        ("Async", 2, 6),
        ("Database", 7, 8),
    ]
    assert "# not a heading" in chunks[1].content
    original = text.splitlines()
    for chunk in chunks:
        assert chunk.content in "\n".join(
            original[chunk.line_start - 1 : chunk.line_end]
        )


def test_long_line_is_bounded_with_overlap_and_original_line_numbers() -> None:
    chunks = split_document("# Long\n" + "a" * 2500)
    assert len(chunks) == 3
    assert all(len(c.content) <= CHUNK_CHARACTERS for c in chunks)
    assert chunks[0].content[-150:] == chunks[1].content[:150]
    assert chunks[-1].line_start == chunks[-1].line_end == 2


def test_fragmentation_limit() -> None:
    with pytest.raises(KnowledgeError, match="too_many_chunks"):
        split_document("# Heading\n" * 257)


def test_bm25_relevance_casefolding_chinese_and_no_hit() -> None:
    documents = [
        "FastAPI 异步接口等待网络响应",
        "Redis 缓存数据库结果",
        "Docker 部署容器",
    ]
    assert rank_chunks("fastapi 异步接口", documents)[0][0] == 0
    assert rank_chunks("Redis 缓存", documents)[0][0] == 1
    assert rank_chunks("docker", documents, 1)[0][0] == 2
    assert rank_chunks("astronomy", documents) == []
    assert rank_chunks("😀", documents) == []
    assert rank_chunks("FastAPI", []) == []


def test_long_irrelevant_text_does_not_dominate_a_precise_match() -> None:
    ranked = rank_chunks("timeout", ["timeout retry", "timeout " + "unrelated " * 1000])
    assert [index for index, _ in ranked] == [0, 1]


def test_long_chunk_covering_both_identifiers_outranks_a_short_partial_match() -> None:
    documents = [
        "quota_bucket quota_bucket",
        "quota_bucket owner_scope quota_bucket owner_scope " + "reference " * 100,
        "owner_scope",
    ]
    ranked = rank_chunks("quota_bucket owner_scope", documents)
    assert ranked[0][0] == 1


def test_exact_technical_phrase_outranks_scattered_terms() -> None:
    documents = [
        "request_id separate timeout",
        "request_id timeout " + "detail " * 100,
    ]
    ranked = rank_chunks("request_id timeout", documents)
    assert ranked[0][0] == 1
