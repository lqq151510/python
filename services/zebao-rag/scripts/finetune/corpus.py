"""把项目文档切成与运行时分块一致的片段，并划分训练/验证来源。

分块规则直接复用 `zebao_rag.chunking`：先按 `##` 标题切，超长正文再用
500 字符窗口、80 字符重叠兜底，块文本格式为「【标题路径】\n正文」。
这样微调用的片段与 worker 建库时的片段完全同构。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT / "src"))

from dataset_snapshot import sha256_bytes  # noqa: E402

# isort: split
from zebao_rag.chunking import _split_by_headings, _window  # noqa: E402

PROJECT_ROOT = RAG_ROOT.parents[0]  # devassist
DEVASSIST_ROOT = RAG_ROOT.parents[1]

DEFAULT_SOURCES = [
    DEVASSIST_ROOT / "README.md",
    DEVASSIST_ROOT / "COLLABORATION.md",
    DEVASSIST_ROOT / "CONTRIBUTING.md",
    DEVASSIST_ROOT / "deployment.md",
    DEVASSIST_ROOT / "deployment-docker-compose.md",
    DEVASSIST_ROOT / "development.md",
]
DEFAULT_DOC_DIR = DEVASSIST_ROOT / "docs"

# 验证集按来源文件整体留出，避免同一文档的片段同时出现在训练集与验证集。
DEFAULT_VAL_SOURCES = {
    "OPERATIONS.md",
    "ZEBAO_RAG_INTEGRATION.md",
    "RETRIEVAL_EVAL_REPORT.md",
}


def chunks_for_file(path: Path) -> list[dict[str, str | int]]:
    md = path.read_text(encoding="utf-8")
    rows: list[dict[str, str | int]] = []
    index = 0
    for heading, body in _split_by_headings(md):
        for piece in _window(body):
            cleaned = piece.strip()
            if not cleaned:
                continue
            rows.append(
                {
                    "id": f"{path.stem}-{index:03d}",
                    "source": path.name,
                    "heading": heading,
                    "index": index,
                    "text": f"【{heading}】\n{cleaned}",
                }
            )
            index += 1
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(RAG_ROOT / ".local" / "finetune"))
    parser.add_argument(
        "--min-chars", type=int, default=60, help="过短的片段不进语料（默认 60）"
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    corpus_path = out_dir / "corpus.jsonl"
    stats_path = out_dir / "corpus_stats.json"
    if any(path.exists() for path in (corpus_path, stats_path, out_dir / "queries.jsonl")):
        parser.error("输出目录已有语料、统计或问题；请指定新的 --out-dir，避免覆盖旧实验")

    files = [path for path in DEFAULT_SOURCES if path.exists()]
    docs_dir = DEFAULT_DOC_DIR
    if docs_dir.exists():
        files.extend(sorted(docs_dir.glob("*.md")))
    if len({path.name for path in files}) != len(files):
        parser.error("来源文件名重复，无法按文件名正确划分训练和验证集")

    chunks: list[dict[str, str | int]] = []
    for path in files:
        chunks.extend(chunks_for_file(path))

    kept = [row for row in chunks if len(str(row["text"])) >= args.min_chars]
    train = [row for row in kept if row["source"] not in DEFAULT_VAL_SOURCES]
    val = [row for row in kept if row["source"] in DEFAULT_VAL_SOURCES]
    ids = [row["id"] for row in kept]
    if len(set(ids)) != len(ids):
        parser.error("片段 ID 重复，请检查来源文件名")
    corpus_bytes = "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in kept
    ).encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    with corpus_path.open("xb") as output:
        output.write(corpus_bytes)

    stats = {
        "files": [path.name for path in files],
        "chunks_total": len(kept),
        "chunks_train": len(train),
        "chunks_val": len(val),
        "dropped_short": len(chunks) - len(kept),
        "val_sources": sorted(DEFAULT_VAL_SOURCES),
        "avg_chars": round(sum(len(str(row["text"])) for row in kept) / max(len(kept), 1)),
        "corpus_path": str(corpus_path),
        "snapshot_version": 1,
        "chunking_method": "zebao_rag_headings_window_500_overlap_80",
        "corpus_sha256": sha256_bytes(corpus_bytes),
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "chunking_sha256": hashlib.sha256(
            Path(sys.modules["zebao_rag.chunking"].__file__).read_bytes()
        ).hexdigest(),
        "min_chars": args.min_chars,
    }
    with stats_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(stats, ensure_ascii=False, indent=2))
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
