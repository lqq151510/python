"""多模态编码器：加载 Qwen3-VL-Embedding，统一编码文本与图片。

关键点：文本和图片走同一个模型、输出同一空间的 2048 维向量，
这是"文字搜图 / 图片搜文字"能成立的根本原因。
"""

import os
from functools import lru_cache

import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

from .config import MODEL_NAME

# 检索指令：只加在“查询”端；入库的文档/图片仍用模型默认指令。
# 该模型 instruction-aware，实测加上后中文“文字搜图”命中率明显提升。
QUERY_INSTRUCTION = "Retrieve relevant images for the query."


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
