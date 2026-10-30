"""给本地嵌入模型 Qwen3-VL-Embedding-2B 的文本塔挂 LoRA，做检索对齐微调。

要点：
- 只在 text backbone 的注意力线性层上挂 LoRA（r=8），视觉塔不参与；
- 损失是 batch 内对称 InfoNCE（query ↔ 正样本），温度 0.05；
- 复用 SentenceTransformer 的 prompt、预处理和池化流程，再做 L2 归一化；
- 训练完把 adapter 存成 PEFT 目录，可用 `SentenceTransformer.load_adapter` 装回，
  推理端由 `zebao_rag.embedder` 决定是否加载。

用法（示例）：
    .venv/bin/python scripts/finetune/train_lora.py --epochs 3 --batch-size 8
    .venv/bin/python scripts/finetune/train_lora.py --dry-run      # 只检查数据管道
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import random
import sys
import time
from pathlib import Path

from dataset_snapshot import read_snapshot

RAG_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"


def load_pairs(work: Path, *, allow_legacy: bool = False) -> list[dict[str, str]]:
    rows, queries, stats, _ = read_snapshot(work, allow_legacy=allow_legacy)
    corpus = {row["id"]: row for row in rows}
    val_sources = set(stats["val_sources"])
    pairs: list[dict[str, str]] = []
    for row in queries:
        chunk = corpus.get(row["chunk_id"])
        if chunk is None or chunk["source"] in val_sources:
            continue
        pairs.append({"query": row["query"], "text": chunk["text"], "chunk_id": row["chunk_id"]})
    return pairs


def batches(items: list[dict[str, str]], size: int, rng: random.Random) -> list[list[dict[str, str]]]:
    shuffled = items[:]
    rng.shuffle(shuffled)
    return [shuffled[index : index + size] for index in range(0, len(shuffled), size)]


def write_training_meta(adapter_dir: Path, metadata: dict) -> Path:
    """Keep provenance beside its adapter; never replace an existing file."""
    path = adapter_dir / "train_meta.json"
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(metadata, ensure_ascii=False, indent=2))
    return path


def training_plan(args: argparse.Namespace, pair_count: int, snapshot: dict) -> dict:
    versions = {}
    for name in ("torch", "transformers", "peft", "sentence-transformers", "numpy", "safetensors"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    return {
        "pairs": pair_count,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "steps_per_epoch": (pair_count + args.batch_size - 1) // args.batch_size,
        "model": args.model,
        "model_artifact_identity_verified": False,
        "model_revision": "unknown",
        "python_version": sys.version.split()[0],
        "dependency_versions": versions,
        "seed": args.seed,
        "max_length": args.max_length,
        "max_steps": args.max_steps,
        "optimizer": "AdamW",
        "weight_decay": 0.0,
        "gradient_clip_norm": 1.0,
        "dataset_snapshot": snapshot,
    }


def resolve_training_protocol(model, query_instruction: str) -> tuple[str, str | None, dict]:
    """Resolve the same prompts used by SentenceTransformer.encode, or fail closed."""
    missing = object()
    name = getattr(model, "default_prompt_name", missing)
    prompts = getattr(model, "prompts", missing)
    resolve = getattr(model, "_resolve_prompt", None)
    truncate_dim = getattr(model, "truncate_dim", missing)
    if name is missing or not isinstance(prompts, dict) or not callable(resolve) or truncate_dim is missing:
        raise ValueError("model prompt configuration cannot be verified")
    if truncate_dim is not None and (not isinstance(truncate_dim, int) or truncate_dim <= 0):
        raise ValueError("model truncate_dim cannot be verified")
    if name is not None and (not isinstance(name, str) or name not in prompts):
        raise ValueError("model default prompt is not resolvable")
    expected = None if name is None else prompts[name]
    if name is not None and not isinstance(expected, str):
        raise ValueError("model default prompt must be a string")
    # SentenceTransformer's encoder resolves its explicit query prompt before
    # preprocessing, and resolves the configured default for documents.
    query_prompt = resolve(query_instruction, None)
    document_prompt = resolve(None, None)
    if query_prompt != query_instruction or document_prompt != expected:
        raise ValueError("model prompt resolution differs from the worker protocol")
    metadata = {
        "version": "worker-text-v1",
        "query_prompt": query_instruction,
        "document_prompt_override": None,
        "document_prompt_status": "model_default_applied" if name is not None else "none",
        "model_default_prompt_name": name,
        "model_default_prompt_sha256": (
            hashlib.sha256(document_prompt.encode("utf-8")).hexdigest()
            if document_prompt is not None
            else None
        ),
        "input_pipeline": "sentence_transformer_preprocess_forward",
        "scope": "prompt_preprocess_and_pooling",
        "numeric_equivalence_to_inference_verified": False,
        "training_backbone_dtype": "bfloat16",
        "training_model_mode": "train",
        "normalization_dtype": "float32",
        "embedding_truncate_dim": truncate_dim,
        "normalize_embeddings": True,
    }
    return query_prompt, document_prompt, metadata


def set_training_max_length(model, max_length: int) -> int | None:
    """Reject models whose tokenizer silently ignores the requested limit."""
    previous = model.max_seq_length
    model.max_seq_length = max_length
    if model.max_seq_length != max_length:
        raise ValueError("model could not apply the requested maximum sequence length")
    return previous


def get_text_backbone(model):
    """Require an input module whose forward uses the exposed backbone."""
    input_module = model[0]
    backbone = getattr(input_module, "auto_model", None)
    if backbone is None or getattr(input_module, "model", None) is not backbone:
        raise ValueError("unsupported SentenceTransformer backbone layout")
    return backbone


def attach_text_backbone(model, backbone) -> None:
    """Attach LoRA to the module actually used by SentenceTransformer.forward."""
    get_text_backbone(model)
    model[0].model = backbone
    if get_text_backbone(model) is not backbone:
        raise ValueError("LoRA backbone was not attached to the forward path")


def embed_training_batch(model, texts: list[str], prompt: str | None, device: str):
    """Use the inference preprocessing/pooling path without disabling gradients."""
    import torch
    from sentence_transformers.util import batch_to_device, truncate_embeddings

    features = batch_to_device(model.preprocess(texts, prompt=prompt), device)
    result = model(features)
    if "sentence_embedding" not in result:
        raise ValueError("model forward did not produce sentence_embedding")
    embeddings = result["sentence_embedding"]
    if model.truncate_dim is not None:
        embeddings = truncate_embeddings(embeddings, model.truncate_dim)
    return torch.nn.functional.normalize(embeddings.float(), dim=1)


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
    parser.add_argument("--allow-legacy-queries", action="store_true")
    parser.add_argument("--adapter-output", type=Path, help="新建的 adapter 目录，不覆盖历史产物")
    args = parser.parse_args()
    if args.max_length <= 0:
        parser.error("--max-length 必须大于 0")

    work = Path(args.work_dir)
    adapter_dir = args.adapter_output or work / "adapter"
    if not args.dry_run and adapter_dir.exists():
        parser.error("adapter 目录已存在，请用 --adapter-output 指定新的目录")
    try:
        pairs = load_pairs(work, allow_legacy=args.allow_legacy_queries)
        _, _, _, snapshot = read_snapshot(work, allow_legacy=args.allow_legacy_queries)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    if len(pairs) < args.batch_size:
        raise SystemExit(f"训练对只有 {len(pairs)} 条，少于一个 batch，先跑 generate_queries.py")
    rng = random.Random(args.seed)
    plan = training_plan(args, len(pairs), snapshot)
    print(json.dumps(plan, ensure_ascii=False), flush=True)
    if args.dry_run:
        sample = batches(pairs, args.batch_size, random.Random(args.seed))[0]
        print(json.dumps({"sample_chunk_id": sample[0]["chunk_id"]}), flush=True)
        return 0

    import torch
    from peft import LoraConfig, get_peft_model
    from sentence_transformers import SentenceTransformer

    from zebao_rag.worker import TEXT_INSTRUCTION

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.manual_seed(args.seed)
    st_model = SentenceTransformer(args.model, device=device)
    try:
        query_prompt, document_prompt, protocol = resolve_training_protocol(
            st_model, TEXT_INSTRUCTION
        )
        runtime_default_max_length = set_training_max_length(st_model, args.max_length)
    except ValueError as exc:
        raise SystemExit(f"无法确认训练编码协议：{exc}") from None
    protocol["model_default_max_seq_length"] = runtime_default_max_length
    protocol["training_max_seq_length"] = args.max_length
    protocol["online_max_seq_length_verified"] = False
    protocol["max_seq_length_matches_model_default"] = (
        args.max_length == runtime_default_max_length
        if isinstance(runtime_default_max_length, int)
        else None
    )
    try:
        backbone = get_text_backbone(st_model)
    except ValueError as exc:
        raise SystemExit(f"无法确认训练模型结构：{exc}") from None
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
    try:
        attach_text_backbone(st_model, backbone)
    except ValueError as exc:
        raise SystemExit(f"无法确认 LoRA 已接入前向路径：{exc}") from None
    backbone.print_trainable_parameters()
    st_model.train()
    trainable = [p for p in backbone.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=0.0)

    history: list[dict[str, float | int]] = []
    global_step = 0
    started = time.time()
    for epoch in range(1, args.epochs + 1):
        for batch in batches(pairs, args.batch_size, rng):
            queries = [row["query"] for row in batch]
            texts = [row["text"] for row in batch]
            q = embed_training_batch(st_model, queries, query_prompt, device)
            p = embed_training_batch(st_model, texts, document_prompt, device)
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

    adapter_dir.mkdir(parents=True, exist_ok=False)
    backbone.save_pretrained(str(adapter_dir))
    meta = {
        **plan,
        "encoding_protocol": protocol,
        "device": device,
        "rank": args.rank,
        "lr": args.lr,
        "steps": global_step,
        "elapsed_s": round(time.time() - started),
        "final_loss": history[-1]["loss"] if history else None,
        "history": history,
        "adapter_dir": str(adapter_dir),
    }
    write_training_meta(adapter_dir, meta)
    print(json.dumps({k: v for k, v in meta.items() if k != "history"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
