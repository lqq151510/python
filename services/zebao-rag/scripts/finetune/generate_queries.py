"""用本机 Ollama 小模型为每个训练片段生成一个中文检索问题。

产物是 `queries.jsonl`，每行 `{"chunk_id", "source", "query"}`。
脚本支持断点续跑：已存在的 chunk_id 会跳过；全部跑完后会以
`keep_alive: 0` 主动卸下模型，把统一内存还给后面的训练。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx
from dataset_snapshot import read_snapshot, text_sha256

RAG_ROOT = Path(__file__).resolve().parents[2]

PROMPT = """你是检索数据集标注员。下面是一段中文项目文档片段，请写 1 个使用者会自然提出的问题。

要求：
- 不要照抄片段里的原句和标题；
- 这个问题必须只靠该片段就能回答；
- 不超过 25 个字，只输出问题本身，不要编号、不要引号、不要解释。

片段：
{text}
"""


def clean(text: str) -> str:
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"^(问题|提问|Q)[:：\s]*", "", text).strip()
    text = text.strip("「」\"'").strip()
    return text


def unload(client: httpx.Client, model: str) -> None:
    try:
        client.post("/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(RAG_ROOT / ".local" / "finetune"))
    parser.add_argument("--model", default="qwen-fast:latest")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（0 表示全部）")
    parser.add_argument("--min-chars", type=int, default=120)
    parser.add_argument(
        "--split",
        choices=("train", "val", "all"),
        default="train",
        help="train=只生成训练片段，val=只生成留出验证片段，all=全部",
    )
    args = parser.parse_args()

    work = Path(args.work_dir)
    try:
        corpus, existing_queries, stats, metadata = read_snapshot(
            work, require_queries=False
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    val_sources = set(stats["val_sources"])

    out_path = work / "queries.jsonl"
    done = {row["chunk_id"] for row in existing_queries}

    def in_split(source: str) -> bool:
        if args.split == "all":
            return True
        if args.split == "val":
            return source in val_sources
        return source not in val_sources

    targets = [
        row
        for row in corpus
        if in_split(row["source"])
        and len(str(row["text"])) >= args.min_chars
        and row["id"] not in done
    ]
    if args.limit:
        targets = targets[: args.limit]

    started = time.time()
    generated = 0
    with httpx.Client(base_url=args.base_url, timeout=300, trust_env=False) as client, out_path.open(
        "a", encoding="utf-8"
    ) as out:
        for position, row in enumerate(targets, start=1):
            response = client.post(
                "/api/chat",
                json={
                    "model": args.model,
                    "messages": [{"role": "user", "content": PROMPT.format(text=row["text"])}],
                    "stream": False,
                    # qwen-fast 是思考模型：不开这个开关时 96 个 token 会全被
                    # 思维链吃掉，content 返回空串，一条问题都拿不到。
                    "think": False,
                    "options": {"temperature": 0.4, "num_predict": 64},
                },
            )
            response.raise_for_status()
            message = response.json().get("message", {})
            query = clean(message.get("content", ""))
            if not 4 <= len(query) <= 40:
                continue
            out.write(
                json.dumps(
                    {"chunk_id": row["id"], "source": row["source"], "query": query,
                     "chunk_sha256": text_sha256(row["text"])},
                    ensure_ascii=False,
                )
                + "\n"
            )
            out.flush()
            generated += 1
            if position % 5 == 0:
                print(
                    f"{position}/{len(targets)} 条，已生成 {generated}，"
                    f"耗时 {time.time() - started:.0f}s",
                    flush=True,
                )
        unload(client, args.model)

    print(
        json.dumps(
            {
                "targets": len(targets),
                "generated": generated,
                "total_queries": len(done) + generated,
                "elapsed_s": round(time.time() - started),
                "path": str(out_path),
                "prior_question_provenance": metadata["question_provenance"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
