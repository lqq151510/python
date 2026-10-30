"""检索前后对比：同一个嵌入模型，adapter 关 vs 开。

两套评估集：
1. `devassist/examples/retrieval-eval.json`（30 个人工问题 / 17 个片段）——
   复用后端 `app.knowledge` 的分块与 BM25（`split_document` / `parse_document` /
   `rank_chunks`），把排序方式换成余弦相似度，仍按 Top1 / Recall@5 / MRR@5 计分，
   并附 BM25 参考值。
2. 仅使用留出文档生成的验证问题，但在训练和验证的全部片段中检索，
   避免候选只包含正确文档造成偏高的指标。

运行前需要先有 `.local/finetune/adapter`；结果写到
`.local/finetune/eval_report_full_corpus_v2.json` 并在终端打印 markdown 表格，
不会覆盖旧协议的实验报告。运行时 cwd 必须是 `backend/`（`app.core.config`
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
import importlib.metadata
import json
import os
import re
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

if "app.models" not in sys.modules:
    _stub = types.ModuleType("app.models")

    class _UnusedTable:
        pass

    _stub.Document = _UnusedTable
    _stub.DocumentChunk = _UnusedTable
    sys.modules["app.models"] = _stub

import numpy as np  # noqa: E402
from app import knowledge  # noqa: E402
from dataset_snapshot import read_snapshot, text_sha256  # noqa: E402

# isort: split
from zebao_rag.identity import artifact_fingerprints  # noqa: E402
from zebao_rag.worker import TEXT_INSTRUCTION  # noqa: E402


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


def load_eval_set(examples_dir: Path, dataset_path: Path, *, include_metadata: bool = False):
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    root = examples_dir.resolve()
    chunks: list[Chunk] = []
    corpus_sha256 = {}
    for relative in dataset["corpus"]:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("invalid corpus path or corpus path outside examples directory")
        raw = path.read_bytes()
        corpus_sha256[relative] = hashlib.sha256(raw).hexdigest()
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
    if include_metadata:
        return dataset, chunks, {
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "corpus_sha256": corpus_sha256,
        }
    return dataset, chunks


def verify_examples_unchanged(examples_dir: Path, dataset_path: Path, frozen: dict) -> None:
    """Fail rather than emit a report for inputs modified during scoring."""
    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != frozen["dataset_sha256"]:
        raise ValueError("evaluation dataset changed during the run; no report written")
    root = examples_dir.resolve()
    for relative, expected in frozen["corpus_sha256"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("evaluation corpus is no longer within the examples directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("evaluation corpus changed during the run; no report written")


def encode(model, texts: list[str], batch_size: int, *, is_query: bool = False) -> np.ndarray:
    # Online worker uses this text-specific instruction for queries only.
    query_options = {"prompt": TEXT_INSTRUCTION} if is_query else {}
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
        **query_options,
    )
    return np.asarray(vectors, dtype=np.float32)


def rank_cosine(candidate_vectors: np.ndarray, query_vector: np.ndarray, limit: int):
    scores = candidate_vectors @ query_vector
    # Stable ties preserve the frozen corpus order across repeated runs.
    order = np.argsort(-scores, kind="stable")[:limit]
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
    unanswerable_nonempty = 0
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
            query_vector = encode(model, [case["query"].strip()], batch_size, is_query=True)[0]
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
            if ranked:
                unanswerable_nonempty += 1
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
        "unanswerable_nonempty_results": unanswerable_nonempty,
        "top1": top1 / answerable if answerable else None,
        "recall_at_5": recall_sum / answerable if answerable else None,
        "mrr_at_5": rr_sum / answerable if answerable else None,
        "unanswerable_top1_at_least_0_5_uncalibrated": unanswerable_high,
        "score_type": "bm25" if chunk_vectors is None else "cosine_similarity",
        "misses": misses,
    }


def val_dataset(work: Path, *, allow_legacy: bool = False, allow_empty_heldout: bool = False):
    corpus, all_queries, stats, metadata = read_snapshot(work, allow_legacy=allow_legacy)
    val_sources = set(stats["val_sources"])
    val_chunks = [row for row in corpus if row["source"] in val_sources]
    valid_ids = {row["id"] for row in val_chunks}
    queries = [row for row in all_queries if row["chunk_id"] in valid_ids]
    if (not val_sources or not val_chunks or not queries) and not allow_empty_heldout:
        raise ValueError(
            "heldout sources, chunks or questions are empty; no heldout evaluation is possible"
        )
    training_hashes = {text_sha256(row["text"]) for row in corpus if row["source"] not in val_sources}
    duplicates = sum(text_sha256(row["text"]) in training_hashes for row in val_chunks)
    metadata.update({
        "candidate_scope": "all_corpus",
        "candidate_segmentation": stats.get("chunking_method", "legacy_unspecified"),
        "candidate_count": len(corpus),
        "heldout_chunk_count": len(val_chunks),
        "heldout_query_count": len(queries),
        "heldout_evaluable": bool(val_sources and val_chunks and queries),
        "exact_cross_split_duplicate_count": duplicates,
        "duplicate_detection": "exact_chunk_text_only",
    })
    return corpus, queries, metadata


def val_metrics(model, chunk_rows: list[dict], queries: list[dict], batch_size: int) -> dict:
    chunk_vectors = encode(model, [row["text"] for row in chunk_rows], batch_size)
    position = {row["id"]: index for index, row in enumerate(chunk_rows)}
    top1 = 0
    recall = 0.0
    rr = 0.0
    misses: list[dict] = []
    for row in queries:
        query_vector = encode(model, [row["query"]], batch_size, is_query=True)[0]
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
        "unanswerable_queries_evaluated": 0,
        "top1": top1 / total,
        "recall_at_5": recall / total,
        "mrr": rr / total,
        "misses": misses,
    }


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def encoding_protocol_metadata(model) -> dict:
    """Report an omitted document prompt separately from a model default."""
    unknown = object()
    name = getattr(model, "default_prompt_name", unknown)
    prompts = getattr(model, "prompts", None)
    prompt = prompts.get(name) if isinstance(name, str) and isinstance(prompts, dict) else None
    if name is unknown:
        status = "unknown"
    elif name is None:
        status = "no_named_default"
    elif isinstance(prompt, str):
        status = "model_default_applied"
    else:
        status = "default_unresolved"
    return {
        "version": "worker-text-v1",
        "query_prompt": TEXT_INSTRUCTION,
        "document_prompt_override": None,
        "document_prompt_status": status,
        "model_default_prompt_name": name if isinstance(name, str) else None,
        "model_default_prompt_sha256": text_sha256(prompt) if isinstance(prompt, str) else None,
        "normalize_embeddings": True,
        "convert_to_numpy": True,
    }


def adapter_provenance(adapter: Path, snapshot: dict, *, allow_unverified: bool) -> str:
    """Check recorded training input; it is not a cryptographic signature."""
    if not adapter.exists():
        return "not_loaded"
    meta_file = adapter / "train_meta.json"
    if not meta_file.is_file():
        if not allow_unverified:
            raise ValueError("adapter lacks training snapshot; use --allow-unverified-adapter to inspect it")
        return "legacy_unverified"
    recorded = json.loads(meta_file.read_bytes()).get("dataset_snapshot", {})
    if not isinstance(recorded, dict):
        raise ValueError("invalid adapter training snapshot metadata")
    expected = recorded.get("training_pairs_sha256")
    if expected is not None and expected != snapshot["training_pairs_sha256"]:
        raise ValueError("adapter training pairs differ from the evaluation snapshot")
    if (
        not expected
        or recorded.get("question_provenance") != "content_verified"
        or snapshot["question_provenance"] != "content_verified"
    ):
        if not allow_unverified:
            raise ValueError("adapter training provenance is unverified; use --allow-unverified-adapter")
        return "legacy_unverified"
    return "training_data_matches_metadata"


def adapter_file_hashes(adapter: Path) -> dict[str, str]:
    """Record adapter bytes once per run, without printing their contents."""
    if adapter.is_symlink():
        raise ValueError("adapter is a symlink; copy to a stable local directory")
    hashes = {}
    for path in sorted(adapter.rglob("*")):
        if path.is_symlink():
            raise ValueError("adapter contains a symlink; copy to a stable local directory")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as src:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[str(path.relative_to(adapter))] = digest.hexdigest()
    return hashes


def resolve_model_snapshot(model: str, *, allow_download: bool) -> Path:
    """Resolve once; model loading must use this exact local directory."""
    local = Path(model).expanduser()
    if local.is_dir():
        return local.resolve(strict=True)
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=model, local_files_only=not allow_download)).resolve(
        strict=True
    )


def checked_artifact_fingerprints(model: Path, adapter: Path | None) -> tuple[str, str | None]:
    """Permit normal HF blob links while preventing traversal into unrelated files."""
    approved_roots = [model.resolve(strict=True)]
    if model.parent.name == "snapshots":
        approved_roots.append((model.parent.parent / "blobs").resolve(strict=False))
    for entry in model.rglob("*"):
        if entry.is_symlink():
            target = entry.resolve(strict=True)
            if not any(target.is_relative_to(root) for root in approved_roots):
                raise ValueError("model snapshot symlink points outside approved artifact roots")
    return artifact_fingerprints(model, adapter)


def model_revision_label(path: Path) -> str:
    """A cache folder name is a label, not a signature or a content digest."""
    if path.parent.name == "snapshots" and re.fullmatch(r"[0-9a-f]{40}", path.name):
        return path.name
    return "unknown"


def dependency_versions() -> dict[str, str]:
    versions = {}
    for name in ("sentence-transformers", "transformers", "torch", "numpy", "safetensors", "peft"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    return versions


def code_snapshot_paths() -> dict[str, Path]:
    return {
        "evaluator": Path(__file__),
        "knowledge": Path(knowledge.__file__),
        "dataset_snapshot": Path(sys.modules[read_snapshot.__module__].__file__),
        "worker_protocol": Path(sys.modules["zebao_rag.worker"].__file__),
        "artifact_identity": Path(sys.modules[artifact_fingerprints.__module__].__file__),
    }


def source_hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


def verify_source_hashes(paths: dict[str, Path], frozen: dict[str, str]) -> None:
    if source_hashes(paths) != frozen:
        raise RuntimeError("evaluation source files changed during run; no report written")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(RAG_ROOT / ".local" / "finetune"))
    parser.add_argument("--model", default="Qwen/Qwen3-VL-Embedding-2B")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="mps")
    parser.add_argument(
        "--allow-model-download", action="store_true",
        help="允许解析模型名时下载缺失权重；默认只使用已有本地文件",
    )
    parser.add_argument("--output", type=Path, help="新的报告路径，已有文件不会被覆盖")
    parser.add_argument(
        "--allow-legacy-queries", action="store_true",
        help="允许内容来源无法验证的旧问题；结果会明确标为 legacy_unverified",
    )
    parser.add_argument(
        "--allow-unverified-adapter", action="store_true",
        help="允许缺少训练快照的历史 adapter；结果将标记为 legacy_unverified",
    )
    parser.add_argument(
        "--allow-empty-heldout", action="store_true",
        help="仅生成诊断集结果；留出问题为空时明确标记为不可评估",
    )
    args = parser.parse_args()

    work = Path(args.work_dir)
    adapter = Path(args.adapter) if args.adapter else work / "adapter"
    output = args.output or work / "eval_report_full_corpus_v2.json"
    if output.exists():
        parser.error("报告已存在，请用 --output 选择新路径，避免覆盖历史结果")
    source_paths = code_snapshot_paths()
    frozen_code_hashes = source_hashes(source_paths)

    try:
        val_chunks, val_queries, snapshot = val_dataset(
            work, allow_legacy=args.allow_legacy_queries,
            allow_empty_heldout=args.allow_empty_heldout,
        )
        if (args.adapter or adapter.exists()) and not adapter.is_dir():
            raise ValueError("specified adapter directory does not exist")
        adapter_hashes = adapter_file_hashes(adapter) if adapter.exists() else {}
        adapter_status = adapter_provenance(
            adapter, snapshot, allow_unverified=args.allow_unverified_adapter
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))

    try:
        model_path = resolve_model_snapshot(args.model, allow_download=args.allow_model_download)
        artifacts_before = checked_artifact_fingerprints(model_path, adapter if adapter.exists() else None)
    except Exception as exc:
        parser.error(f"模型快照身份无法验证：{type(exc).__name__}")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(model_path), device=args.device, local_files_only=True)
    if checked_artifact_fingerprints(model_path, adapter if adapter.exists() else None) != artifacts_before:
        raise RuntimeError("model or adapter files changed during base model loading; no report written")
    examples_dir = DEVASSIST_ROOT / "examples"
    example_dataset_path = examples_dir / "retrieval-eval.json"
    dataset, chunks, examples_snapshot = load_eval_set(
        examples_dir, example_dataset_path, include_metadata=True
    )
    cases = dataset["cases"]
    chunk_texts = [chunk.text for chunk in chunks]
    dataset_sha = examples_snapshot["dataset_sha256"]

    bm25 = score_cases(cases, chunks, None, None, args.batch_size)
    baseline_vectors = encode(model, chunk_texts, args.batch_size)
    baseline = score_cases(cases, chunks, baseline_vectors, model, args.batch_size)

    baseline_val = (
        val_metrics(model, val_chunks, val_queries, args.batch_size) if val_queries else None
    )

    tuned = tuned_val = None
    loaded = adapter.exists()
    if loaded:
        model.load_adapter(str(adapter))
        if checked_artifact_fingerprints(model_path, adapter) != artifacts_before:
            raise RuntimeError("model or adapter files changed during adapter loading; no report written")
        if adapter_file_hashes(adapter) != adapter_hashes:
            raise RuntimeError("adapter files changed during loading; evaluation aborted")
        tuned_vectors = encode(model, chunk_texts, args.batch_size)
        tuned = score_cases(cases, chunks, tuned_vectors, model, args.batch_size)
        if val_queries:
            tuned_val = val_metrics(model, val_chunks, val_queries, args.batch_size)
        if adapter_file_hashes(adapter) != adapter_hashes:
            raise RuntimeError("adapter files changed during evaluation; no report written")
    if checked_artifact_fingerprints(model_path, adapter if loaded else None) != artifacts_before:
        raise RuntimeError("model or adapter files changed during evaluation; no report written")
    verify_examples_unchanged(examples_dir, example_dataset_path, examples_snapshot)
    verify_source_hashes(source_paths, frozen_code_hashes)

    report = {
        "model": args.model,
        "model_load_mode": "resolved_local_directory_only",
        "model_download_allowed": args.allow_model_download,
        "dependency_versions": dependency_versions(),
        "python_version": sys.version.split()[0],
        "device": args.device,
        "batch_size": args.batch_size,
        "adapter": str(adapter) if loaded else None,
        "adapter_training_data_provenance": adapter_status,
        "adapter_file_sha256": adapter_hashes,
        "encoding_protocol": encoding_protocol_metadata(model),
        "dataset_sha256": dataset_sha,
        "examples_corpus_sha256": examples_snapshot["corpus_sha256"],
        "evaluator_sha256": frozen_code_hashes["evaluator"],
        "knowledge_sha256": frozen_code_hashes["knowledge"],
        "source_sha256": frozen_code_hashes,
        "source_identity_limit": "Disk bytes captured at run start, not a digest of in-memory bytecode.",
        "model_revision": model_revision_label(model_path),
        "model_revision_source": "cache_directory_label_not_signature",
        "model_artifact_sha256": artifacts_before[0],
        "adapter_artifact_sha256": artifacts_before[1],
        "model_artifact_content_checked": True,
        "cross_run_model_identity_verified": False,
        "cross_run_comparison_limit": (
            "Compare only runs with matching artifact hashes, encoding protocol, "
            "dependencies, data snapshots and evaluation settings. "
            "A cache revision label is not a cryptographic signature; hashes do not "
            "rule out change-and-restore races during model loading or establish "
            "the identity of in-memory weights or external referenced artifacts."
        ),
        "snapshot": snapshot,
        "metric_notes": {
            "unanswerable_top1_at_least_0_5_uncalibrated": (
                "Diagnostic score threshold only; BM25 and cosine scores are not "
                "comparable, and this does not measure answer correctness or refusal."
            ),
            "val_docs": "Positive heldout questions only; no refusal or negative evaluation.",
            "val_docs_chunking": (
                "Prechunked snapshot; corpus.py uses worker heading/windows 500/80, "
                "while live backend knowledge.split_document uses 1000/150. "
                "Do not interpret this as online end-to-end evaluation."
            ),
            "val_docs_input": (
                "Scores prechunked row.text only; the backend sends filename, heading, "
                "and chunk content together. This is not the same text composition."
            ),
        },
        "eval_dataset": {
            "chunk_count": len(chunks),
            "bm25": bm25,
            "baseline": baseline,
            "tuned": tuned,
        },
        "val_docs": {"baseline": baseline_val, "tuned": tuned_val},
    }
    with output.open("x", encoding="utf-8") as out:
        out.write(json.dumps(report, ensure_ascii=False, indent=2))

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
