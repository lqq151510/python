"""Integrity checks for the local fine-tuning corpus and generated questions.

No model or database access is needed. Legacy datasets can be inspected only
when explicitly requested; they cannot prove that questions match their chunks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def text_sha256(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def read_snapshot(work: Path, *, allow_legacy: bool = False, require_queries: bool = True):
    corpus_bytes = (work / "corpus.jsonl").read_bytes()
    query_path = work / "queries.jsonl"
    query_bytes = query_path.read_bytes() if require_queries or query_path.exists() else b""
    stats_bytes = (work / "corpus_stats.json").read_bytes()
    stats = json.loads(stats_bytes)
    corpus = [json.loads(line) for line in corpus_bytes.splitlines() if line.strip()]
    queries = [json.loads(line) for line in query_bytes.splitlines() if line.strip()]
    expected_corpus_hash = stats.get("corpus_sha256")
    legacy = expected_corpus_hash is None
    if expected_corpus_hash is not None and expected_corpus_hash != sha256_bytes(corpus_bytes):
        raise ValueError("corpus.jsonl changed since its snapshot was created")

    by_id = {}
    for row in corpus:
        identifier = row["id"]
        if identifier in by_id:
            raise ValueError(f"duplicate chunk id: {identifier}")
        by_id[identifier] = row
    seen = set()
    for row in queries:
        identifier = row["chunk_id"]
        if identifier in seen:
            raise ValueError(f"duplicate question for chunk: {identifier}")
        seen.add(identifier)
        chunk = by_id.get(identifier)
        if chunk is None:
            raise ValueError(f"question references missing chunk: {identifier}")
        if row.get("source") != chunk["source"]:
            raise ValueError(f"question source differs from chunk: {identifier}")
        recorded = row.get("chunk_sha256")
        if recorded is None:
            legacy = True
        elif recorded != text_sha256(chunk["text"]):
            raise ValueError(f"question no longer matches chunk content: {identifier}")
    if legacy and not allow_legacy:
        raise ValueError(
            "legacy snapshot lacks corpus/question hashes; create a new snapshot or "
            "explicitly use --allow-legacy-queries (results will be marked unverified)"
        )
    val_sources = set(stats["val_sources"])
    # Hash actual training examples, not the questions file as a whole: heldout
    # questions may be added after training without changing the training input.
    training_pairs = sorted(
        (
            {"chunk_id": row["chunk_id"], "query": row["query"],
             "text": by_id[row["chunk_id"]]["text"]}
            for row in queries if by_id[row["chunk_id"]]["source"] not in val_sources
        ),
        key=lambda item: item["chunk_id"],
    )
    training_bytes = json.dumps(
        training_pairs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return corpus, queries, stats, {
        "corpus_sha256": sha256_bytes(corpus_bytes),
        "queries_sha256": sha256_bytes(query_bytes),
        "stats_sha256": sha256_bytes(stats_bytes),
        "training_pairs_sha256": sha256_bytes(training_bytes),
        "training_pair_count": len(training_pairs),
        "question_provenance": "legacy_unverified" if legacy else "content_verified",
    }
