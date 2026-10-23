"""给本地嵌入模型 Qwen3-VL-Embedding-2B 的文本塔挂 LoRA，做检索对齐微调。

要点：
- 只在 text backbone 的注意力线性层上挂 LoRA（r=8），视觉塔不参与；
- 损失是 batch 内对称 InfoNCE（query ↔ 正样本），温度 0.05；
- 池化与 `1_Pooling/config.json` 一致：lasttoken（取最后一个非 pad token）+ L2 归一化；
- 训练完把 adapter 存成 PEFT 目录，可用 `SentenceTransformer.load_adapter` 装回，
  推理端由 `zebao_rag.embedder` 决定是否加载。

用法（示例）：
    .venv/bin/python scripts/finetune/train_lora.py --epochs 3 --batch-size 8
    .venv/bin/python scripts/finetune/train_lora.py --dry-run      # 只检查数据管道
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"


def load_pairs(work: Path) -> list[dict[str, str]]:
    corpus = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (work / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    stats = json.loads((work / "corpus_stats.json").read_text(encoding="utf-8"))
    val_sources = set(stats["val_sources"])
    pairs: list[dict[str, str]] = []
    for line in (work / "queries.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        chunk = corpus.get(row["chunk_id"])
        if chunk is None or chunk["source"] in val_sources:
            continue
        pairs.append({"query": row["query"], "text": chunk["text"], "chunk_id": row["chunk_id"]})
    return pairs


def batches(items: list[dict[str, str]], size: int, rng: random.Random) -> list[list[dict[str, str]]]:
    shuffled = items[:]
    rng.shuffle(shuffled)
    return [shuffled[index : index + size] for index in range(0, len(shuffled), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(RAG_ROOT / ".local" / "finetune"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--rank", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--max-steps", type=int, default=0, help="只跑前 N 步（冒烟用）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    work = Path(args.work_dir)
    pairs = load_pairs(work)
    if len(pairs) < args.batch_size:
        raise SystemExit(f"训练对只有 {len(pairs)} 条，少于一个 batch，先跑 generate_queries.py")
    rng = random.Random(args.seed)
    plan = {
        "pairs": len(pairs),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "steps_per_epoch": (len(pairs) + args.batch_size - 1) // args.batch_size,
        "model": args.model,
    }
    print(json.dumps(plan, ensure_ascii=False), flush=True)
    if args.dry_run:
        sample = batches(pairs, args.batch_size, random.Random(args.seed))[0]
        print(json.dumps(sample[0], ensure_ascii=False)[:400], flush=True)
        return 0

    import torch
    from peft import LoraConfig, get_peft_model
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.manual_seed(args.seed)
    st_model = SentenceTransformer(args.model, device=device)
    st_model.max_seq_length = args.max_length
    backbone = getattr(st_model[0], "auto_model", None)
    if backbone is None:
        raise SystemExit("SentenceTransformer 的第一个模块没有 auto_model，无法挂 LoRA")
    backbone = backbone.to(dtype=torch.bfloat16)
    lora = LoraConfig(
        r=args.rank,
        lora_alpha=args.rank * 2,
        lora_dropout=0.05,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        task_type="FEATURE_EXTRACTION",
    )
    backbone = get_peft_model(backbone, lora)
    st_model[0].auto_model = backbone
    backbone.print_trainable_parameters()
    tokenizer = st_model.tokenizer
    trainable = [p for p in backbone.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=0.0)

    def embed(texts: list[str]):
        encoded = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=args.max_length,
            return_tensors="pt",
        ).to(device)
        outputs = backbone(
            input_ids=encoded["input_ids"],
            attention_mask=encoded["attention_mask"],
            output_hidden_states=True,
        )
        hidden = outputs.hidden_states[-1] if outputs.hidden_states is not None else outputs.last_hidden_state
        mask = encoded["attention_mask"]
        last_index = mask.sum(dim=1) - 1
        pooled = hidden[torch.arange(hidden.size(0), device=hidden.device), last_index]
        return torch.nn.functional.normalize(pooled.float(), dim=1)

    history: list[dict[str, float | int]] = []
    global_step = 0
    started = time.time()
    for epoch in range(1, args.epochs + 1):
        for batch in batches(pairs, args.batch_size, rng):
            queries = [row["query"] for row in batch]
            texts = [row["text"] for row in batch]
            q = embed(queries)
            p = embed(texts)
            logits = q @ p.T * 20.0
            labels = torch.arange(len(batch), device=logits.device)
            loss = (
                torch.nn.functional.cross_entropy(logits, labels)
                + torch.nn.functional.cross_entropy(logits.T, labels)
            ) / 2
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            optimizer.step()
            global_step += 1
            if global_step % 5 == 0 or global_step == 1:
                record = {
                    "step": global_step,
                    "epoch": epoch,
                    "loss": round(float(loss.detach().cpu()), 4),
                    "elapsed_s": round(time.time() - started),
                }
                history.append(record)
                print(json.dumps(record), flush=True)
            if args.max_steps and global_step >= args.max_steps:
                break
        if args.max_steps and global_step >= args.max_steps:
            break

    adapter_dir = work / "adapter"
    backbone.save_pretrained(str(adapter_dir))
    adapter_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        **plan,
        "device": device,
        "rank": args.rank,
        "lr": args.lr,
        "steps": global_step,
        "elapsed_s": round(time.time() - started),
        "final_loss": history[-1]["loss"] if history else None,
        "history": history,
        "adapter_dir": str(adapter_dir),
    }
    (work / "train_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in meta.items() if k != "history"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
