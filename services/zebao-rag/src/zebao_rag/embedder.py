"""多模态编码器：加载 Qwen3-VL-Embedding，统一编码文本与图片。

关键点：文本和图片走同一个模型、输出同一空间的 2048 维向量，
这是"文字搜图 / 图片搜文字"能成立的根本原因。
"""

import os
from functools import lru_cache

# transformers 5.x 默认用线程池并发加载权重；在 Apple Silicon + MPS 上
# load_adapter() 偶发 SIGSEGV（exit 139），线程池里做 _materialize_copy 时崩。
# 这个开关必须在 import transformers 之前设好，改成主线程同步加载后稳定通过。
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "true")

import torch  # noqa: E402
from PIL import Image  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

from .config import MODEL_NAME  # noqa: E402

# 检索指令：只加在“查询”端；入库的文档/图片仍用模型默认指令。
# 该模型 instruction-aware，实测加上后中文“文字搜图”命中率明显提升。
QUERY_INSTRUCTION = "Retrieve relevant images for the query."

# 可选 LoRA adapter 目录（scripts/finetune/train_lora.py 的产物）。
# 训练只用了文本对，图片向量会跟着变，所以切换后必须重新 build 索引，
# 否则库里的旧向量和新查询向量不在同一个空间里。
ADAPTER_PATH = os.environ.get("ZEBAO_RAG_ADAPTER", "").strip()


def pick_device() -> str:
    """优先 Apple GPU(MPS)，其次 NVIDIA(CUDA)，最后 CPU。"""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """全局只加载一次模型（首次会从 HuggingFace 下载，约 4.5GB）。"""
    device = pick_device()
    model = SentenceTransformer(
        MODEL_NAME,
        device=device,
        local_files_only=os.environ.get("HF_HUB_OFFLINE", "0").lower()
        in {"1", "true", "yes"},
    )
    if ADAPTER_PATH:
        from pathlib import Path

        if not Path(ADAPTER_PATH).is_dir():
            raise RuntimeError(f"ZEBAO_RAG_ADAPTER 指向的目录不存在：{ADAPTER_PATH}")
        model.load_adapter(ADAPTER_PATH)
    return model


def encode_texts(
    texts: list[str], is_query: bool = False, *, instruction: str | None = None
) -> list[list[float]]:
    """编码文本。is_query=True 时加检索指令（用于用户提问端）。"""
    model = get_model()
    kwargs = {"prompt": instruction or QUERY_INSTRUCTION} if is_query else {}
    vec = model.encode(
        texts,
        normalize_embeddings=True,  # 归一化后点积即余弦相似度
        convert_to_numpy=True,
        show_progress_bar=len(texts) > 8,
        **kwargs,
    )
    return vec.tolist()


def encode_images(paths: list[str]) -> list[list[float]]:
    model = get_model()
    imgs = [Image.open(p).convert("RGB") for p in paths]
    vec = model.encode(
        imgs,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=len(paths) > 8,
    )
    return vec.tolist()
