"""Offline baseline for the repository's two synthetic retrieval datasets.

Only repository examples and the explicitly resolved local model snapshot are
read; no adapter, training work directory, database, or real model API is used.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import socket
import sys
import time
from pathlib import Path


def deny_network(*_args, **_kwargs):
    raise RuntimeError("network disabled for offline retrieval evaluation")


def disable_network() -> None:
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="已有本地模型目录或已缓存的模型名")
    parser.add_argument("--output", type=Path, required=True, help="新报告文件，不能已存在")
    parser.add_argument("--device", choices=("mps", "cpu", "cuda"), default="mps")
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    if not args.output.parent.is_dir():
        parser.error("output parent directory does not exist")

    # Set the guard before importing model or evaluation dependencies.
    disable_network()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_retrieval as evaluation

    paths = evaluation.code_snapshot_paths()
    paths["examples_runner"] = Path(__file__)
    frozen_code = evaluation.source_hashes(paths)
    examples = evaluation.DEVASSIST_ROOT / "examples"
    names = ("retrieval-eval.json", "retrieval-eval-holdout.json")
    datasets = []
    for name in names:
        dataset, chunks, frozen = evaluation.load_eval_set(
            examples, examples / name, include_metadata=True
        )
        datasets.append((name, dataset, chunks, frozen))
    first_chunks = datasets[0][2]
    def signature(chunks):
        return [
            (c.filename, c.position, c.heading, c.line_start, c.line_end, c.content)
            for c in chunks
        ]
    if signature(first_chunks) != signature(datasets[1][2]):
        parser.error("datasets do not share the same ordered corpus")
    try:
        model_path = evaluation.resolve_model_snapshot(args.model, allow_download=False)
        artifacts = evaluation.checked_artifact_fingerprints(model_path, None)
    except Exception as exc:
        parser.error(f"local model snapshot unavailable: {type(exc).__name__}")

    if args.device == "mps":
        import torch

        if not torch.backends.mps.is_available():
            parser.error("MPS unavailable; choose another device explicitly")
        torch.mps.set_per_process_memory_fraction(0.45)
    from sentence_transformers import SentenceTransformer

    started = time.monotonic()
    model = SentenceTransformer(str(model_path), device=args.device, local_files_only=True)
    if evaluation.checked_artifact_fingerprints(model_path, None) != artifacts:
        raise RuntimeError("model files changed during loading; no report written")
    vectors = evaluation.encode(model, [chunk.text for chunk in first_chunks], args.batch_size)
    results = []
    for name, dataset, chunks, frozen in datasets:
        began = time.monotonic()
        results.append({
            "filename": name,
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": frozen["dataset_sha256"],
            "corpus_sha256": frozen["corpus_sha256"],
            "candidate_count": len(chunks),
            "case_count": len(dataset["cases"]),
            "bm25": evaluation.score_cases(dataset["cases"], chunks, None, None, args.batch_size),
            "embedding": evaluation.score_cases(dataset["cases"], chunks, vectors, model, args.batch_size),
            "elapsed_seconds": round(time.monotonic() - began, 3),
        })
        print(f"dataset complete: {name} ({len(dataset['cases'])} cases)", flush=True)
    for name, _, _, frozen in datasets:
        evaluation.verify_examples_unchanged(examples, examples / name, frozen)
    evaluation.verify_source_hashes(paths, frozen_code)
    if evaluation.checked_artifact_fingerprints(model_path, None) != artifacts:
        raise RuntimeError("model files changed during evaluation; no report written")

    report = {
        "purpose": "synthetic retrieval baselines, not generated answers or refusal evaluation",
        "network": "Python socket connections and DNS blocked; HF offline/local files only",
        "dataset_relationship": "independently annotated holdout questions share the same five synthetic documents; not an independent corpus",
        "model_revision": evaluation.model_revision_label(model_path),
        "model_revision_source": "cache_directory_label_not_signature",
        "model_artifact_sha256": artifacts[0],
        "cross_run_model_identity_verified": False,
        "model_identity_limit": "disk hashes around loading/scoring do not prove in-memory weights or external artifacts",
        "adapter": "not loaded",
        "device": args.device,
        "batch_size": args.batch_size,
        "encoding_protocol": evaluation.encoding_protocol_metadata(model),
        "dependency_versions": evaluation.dependency_versions(),
        "python_version": sys.version.split()[0],
        "source_sha256": frozen_code,
        "source_identity_limit": "disk bytes frozen at run start, not in-memory bytecode",
        "case_total": sum(item["case_count"] for item in results),
        "datasets": results,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_process_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if sys.platform == "darwin" else None,
        "mps_allocated_bytes_at_end": torch.mps.current_allocated_memory() if args.device == "mps" else None,
        "limitations": [
            "synthetic data and shared corpus",
            "no generated answer, refusal or hallucination evaluated",
            "0.5 score threshold is uncalibrated; BM25 and cosine scores are not comparable",
            "no adapter, real database or online worker evaluated",
            "MPS allocation at end is not peak GPU memory",
        ],
    }
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(args.output), "case_total": report["case_total"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
