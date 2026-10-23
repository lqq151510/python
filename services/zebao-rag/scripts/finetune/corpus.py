"""把项目文档切成与运行时分块一致的片段，并划分训练/验证来源。

分块规则直接复用 `zebao_rag.chunking`：先按 `##` 标题切，超长正文再用
500 字符窗口、80 字符重叠兜底，块文本格式为「【标题路径】\n正文」。
这样微调用的片段与 worker 建库时的片段完全同构。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT / "src"))

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
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [path for path in DEFAULT_SOURCES if path.exists()]
    docs_dir = DEFAULT_DOC_DIR
    if docs_dir.exists():
        files.extend(sorted(docs_dir.glob("*.md")))

    chunks: list[dict[str, str | int]] = []
    for path in files:
        chunks.extend(chunks_for_file(path))

    kept = [row for row in chunks if len(str(row["text"])) >= args.min_chars]
    train = [row for row in kept if row["source"] not in DEFAULT_VAL_SOURCES]
    val = [row for row in kept if row["source"] in DEFAULT_VAL_SOURCES]

    corpus_path = out_dir / "corpus.jsonl"
    with corpus_path.open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats = {
        "files": [path.name for path in files],
        "chunks_total": len(kept),
        "chunks_train": len(train),
        "chunks_val": len(val),
        "dropped_short": len(chunks) - len(kept),
        "val_sources": sorted(DEFAULT_VAL_SOURCES),
        "avg_chars": round(sum(len(str(row["text"])) for row in kept) / max(len(kept), 1)),
        "corpus_path": str(corpus_path),
    }
    (out_dir / "corpus_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
