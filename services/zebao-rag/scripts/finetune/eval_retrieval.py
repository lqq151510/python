"""检索前后对比：同一个嵌入模型，adapter 关 vs 开。

两套评估集：
1. `devassist/examples/retrieval-eval.json`（30 个人工问题 / 17 个片段）——
   复用后端 `app.knowledge` 的分块与 BM25（`split_document` / `parse_document` /
   `rank_chunks`），把排序方式换成余弦相似度，仍按 Top1 / Recall@5 / MRR@5 计分，
   并附 BM25 参考值。
2. 项目文档留出的验证源（`corpus_stats.json` 的 `val_sources`）+ 这些片段的
   生成问题，考察同领域未见过文档上的检索。

运行前需要先有 `.local/finetune/adapter`；结果写到 `.local/finetune/eval_report.json`
并在终端打印 markdown 表格。运行时 cwd 必须是 `backend/`（`app.core.config`
按相对路径读取 `../.env`）。

说明：脚本跑在 RAG 服务的 Python 3.12 环境里，而 backend 用的是 3.14。
`app.retrieval_eval` 里的 `class Evidence(StrictModel)` 用了类自身做返回标注，
3.12 下会在定义时就求值失败，所以这里不导入它，只复用 `app.knowledge`
的纯函数，并在导入前用占位模块顶掉 `app.models`（那里有 SQLModel 表，
评估用不到）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import types
from pathlib import Path

# 与 src/zebao_rag/embedder.py 保持一致：transformers 5.x 的线程池加载在
# Apple Silicon + MPS 上加载 adapter 时会偶发 SIGSEGV，改主线程同步加载。
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "true")

RAG_ROOT = Path(__file__).resolve().parents[2]
DEVASSIST_ROOT = RAG_ROOT.parents[1]
BACKEND_ROOT = DEVASSIST_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

if "app.models" not in sys.modules:
    _stub = types.ModuleType("app.models")

    class _UnusedTable:
        pass

    _stub.Document = _UnusedTable
    _stub.DocumentChunk = _UnusedTable
    sys.modules["app.models"] = _stub

import numpy as np  # noqa: E402

from app import knowledge  # noqa: E402


class Chunk:
    __slots__ = ("filename", "position", "heading", "line_start", "line_end", "content")

    def __init__(self, filename, position, heading, line_start, line_end, content):
        self.filename = filename
        self.position = position
        self.heading = heading
        self.line_start = line_start
        self.line_end = line_end
        self.content = content

    def matches(self, evidence: dict) -> bool:
        return (
            self.filename == evidence["filename"]
            and self.line_start <= evidence["line_start"]
            and self.line_end >= evidence["line_end"]
            and evidence["quote"] in self.content
        )

    @property
    def text(self) -> str:
        return f"{self.filename}\n{self.heading}\n{self.content}"


def load_eval_set(examples_dir: Path, dataset_path: Path):
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    root = examples_dir.resolve()
    chunks: list[Chunk] = []
    for relative in dataset["corpus"]:
        path = (root / relative).resolve()
        raw = path.read_bytes()
        filename, text, _ = knowledge.parse_document(path.name, raw)
        for position, piece in enumerate(knowledge.split_document(text)):
            chunks.append(
                Chunk(
                    filename,
                    position,
                    piece.heading,
                    piece.line_start,
                    piece.line_end,
                    piece.content,
                )
            )
    return dataset, chunks


def encode(model, texts: list[str], batch_size: int) -> np.ndarray:
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def rank_cosine(candidate_vectors: np.ndarray, query_vector: np.ndarray, limit: int):
    scores = candidate_vectors @ query_vector
    order = np.argsort(-scores)[:limit]
    return [(int(index), float(scores[index])) for index in order]


def rank_bm25(case: dict, candidates: list[Chunk], limit: int):
    ranked = knowledge.rank_chunks(
        case["query"].strip(), [chunk.text for chunk in candidates], limit=limit
    )
    return ranked


def score_cases(
    cases: list[dict],
    chunks: list[Chunk],
    chunk_vectors: np.ndarray | None,
    model,
    batch_size: int,
) -> dict:
    top1 = 0
    recall_sum = 0.0
    rr_sum = 0.0
    answerable = 0
    unanswerable = 0
    unanswerable_high = 0
    misses: list[dict] = []
    for case in cases:
        candidate_index = [
            index
            for index, chunk in enumerate(chunks)
            if case["scope"] is None or chunk.filename == case["scope"]
        ]
        candidates = [chunks[index] for index in candidate_index]
        if chunk_vectors is None:
            ranked = rank_bm25(case, candidates, 5)
        else:
            query_vector = encode(model, [case["query"].strip()], batch_size)[0]
            ranked = rank_cosine(chunk_vectors[candidate_index], query_vector, 5)
        hit_ids: set[int] = set()
        first_hit = None
        for rank, (position, _score) in enumerate(ranked, 1):
            matched = [
                number
                for number, gold in enumerate(case["expected"], 1)
                if candidates[position].matches(gold)
            ]
            if matched:
                hit_ids.update(matched)
                if first_hit is None:
                    first_hit = rank
        if not case["expected"]:
            unanswerable += 1
            if ranked and ranked[0][1] >= 0.5:
                unanswerable_high += 1
            continue
        answerable += 1
        if first_hit == 1:
            top1 += 1
        else:
            misses.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "top1": candidates[ranked[0][0]].filename if ranked else None,
                    "first_hit_rank": first_hit,
                }
            )
        recall_sum += len(hit_ids) / len(case["expected"])
        rr_sum += 1 / first_hit if first_hit else 0.0
    return {
        "answerable": answerable,
        "unanswerable": unanswerable,
        "top1": top1 / answerable if answerable else None,
        "recall_at_5": recall_sum / answerable if answerable else None,
        "mrr_at_5": rr_sum / answerable if answerable else None,
        "unanswerable_above_0.5": unanswerable_high,
        "misses": misses,
    }


def val_dataset(work: Path):
    stats = json.loads((work / "corpus_stats.json").read_text(encoding="utf-8"))
    val_sources = set(stats["val_sources"])
    corpus = [
        json.loads(line)
        for line in (work / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    val_chunks = [row for row in corpus if row["source"] in val_sources]
    valid_ids = {row["id"] for row in val_chunks}
    queries = []
    for line in (work / "queries.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["chunk_id"] in valid_ids:
            queries.append(row)
    return val_chunks, queries


def val_metrics(model, chunk_rows: list[dict], queries: list[dict], batch_size: int) -> dict:
    chunk_vectors = encode(model, [row["text"] for row in chunk_rows], batch_size)
    position = {row["id"]: index for index, row in enumerate(chunk_rows)}
    top1 = 0
    recall = 0.0
    rr = 0.0
    misses: list[dict] = []
    for row in queries:
        query_vector = encode(model, [row["query"]], batch_size)[0]
        ranked = rank_cosine(chunk_vectors, query_vector, 5)
        gold = position[row["chunk_id"]]
        ranks = [rank for rank, (index, _score) in enumerate(ranked, 1) if index == gold]
        rank = ranks[0] if ranks else None
        top1 += rank == 1
        recall += bool(rank and rank <= 5)
        rr += 1 / rank if rank else 0.0
        if rank != 1:
            misses.append({"query": row["query"], "rank": rank})
    total = max(len(queries), 1)
    return {
        "queries": len(queries),
        "chunks": len(chunk_rows),
        "top1": top1 / total,
        "recall_at_5": recall / total,
        "mrr": rr / total,
        "misses": misses,
    }


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(RAG_ROOT / ".local" / "finetune"))
    parser.add_argument("--model", default="Qwen/Qwen3-VL-Embedding-2B")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()

    work = Path(args.work_dir)
    adapter = Path(args.adapter) if args.adapter else work / "adapter"

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(args.model, device=args.device)
    examples_dir = DEVASSIST_ROOT / "examples"
    dataset, chunks = load_eval_set(examples_dir, examples_dir / "retrieval-eval.json")
    cases = dataset["cases"]
    chunk_texts = [chunk.text for chunk in chunks]
    dataset_sha = hashlib.sha256((examples_dir / "retrieval-eval.json").read_bytes()).hexdigest()

    bm25 = score_cases(cases, chunks, None, None, args.batch_size)
    baseline_vectors = encode(model, chunk_texts, args.batch_size)
    baseline = score_cases(cases, chunks, baseline_vectors, model, args.batch_size)

    val_chunks, val_queries = val_dataset(work)
    baseline_val = (
        val_metrics(model, val_chunks, val_queries, args.batch_size) if val_queries else None
    )

    tuned = tuned_val = None
    loaded = adapter.exists()
    if loaded:
        model.load_adapter(str(adapter))
        tuned_vectors = encode(model, chunk_texts, args.batch_size)
        tuned = score_cases(cases, chunks, tuned_vectors, model, args.batch_size)
        if val_queries:
            tuned_val = val_metrics(model, val_chunks, val_queries, args.batch_size)

    report = {
        "model": args.model,
        "adapter": str(adapter) if loaded else None,
        "dataset_sha256": dataset_sha,
        "eval_dataset": {
            "chunk_count": len(chunks),
            "bm25": bm25,
            "baseline": baseline,
            "tuned": tuned,
        },
        "val_docs": {"baseline": baseline_val, "tuned": tuned_val},
    }
    (work / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "| 评估集 | 方案 | Top1 | Recall@5 | MRR@5 |",
        "| --- | --- | --- | --- | --- |",
        f"| examples 30 问 | BM25（现有） | {fmt(bm25['top1'])} | "
        f"{fmt(bm25['recall_at_5'])} | {fmt(bm25['mrr_at_5'])} |",
        f"| examples 30 问 | 嵌入基线 | {fmt(baseline['top1'])} | "
        f"{fmt(baseline['recall_at_5'])} | {fmt(baseline['mrr_at_5'])} |",
    ]
    if tuned:
        lines.append(
            f"| examples 30 问 | 嵌入 + LoRA | {fmt(tuned['top1'])} | "
            f"{fmt(tuned['recall_at_5'])} | {fmt(tuned['mrr_at_5'])} |"
        )
    if baseline_val:
        lines.append(
            f"| 项目文档验证集 {baseline_val['queries']} 问 | 嵌入基线 | "
            f"{fmt(baseline_val['top1'])} | {fmt(baseline_val['recall_at_5'])} | "
            f"{fmt(baseline_val['mrr'])} |"
        )
    if tuned_val:
        lines.append(
            f"| 项目文档验证集 {tuned_val['queries']} 问 | 嵌入 + LoRA | "
            f"{fmt(tuned_val['top1'])} | {fmt(tuned_val['recall_at_5'])} | "
            f"{fmt(tuned_val['mrr'])} |"
        )
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
