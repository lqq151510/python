"""多模态编码器：加载 Qwen3-VL-Embedding，统一编码文本与图片。

关键点：文本和图片走同一个模型、输出同一空间的 2048 维向量，
这是"文字搜图 / 图片搜文字"能成立的根本原因。
"""

import os
from functools import lru_cache
from io import BytesIO

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
_WORKER_MODEL_PATH: str | None = None
_WORKER_ADAPTER_PATH: str | None = None


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
        _WORKER_MODEL_PATH or MODEL_NAME,
        device=device,
        local_files_only=bool(_WORKER_MODEL_PATH) or os.environ.get("HF_HUB_OFFLINE", "0").lower()
        in {"1", "true", "yes"},
    )
    adapter_path = _WORKER_ADAPTER_PATH or ADAPTER_PATH
    if adapter_path:
        from pathlib import Path

        if not Path(adapter_path).is_dir():
            raise RuntimeError("ZEBAO_RAG_ADAPTER 指向的目录不存在")
        model.load_adapter(adapter_path)
    return model


def get_verified_identity(text_instruction: str) -> dict[str, str]:
    """Pin local artifacts, load them, and reject changes during loading.

    A download is allowed only when HF_HUB_OFFLINE=0 was explicitly selected.
    The resulting local snapshot is fixed before hashing and loading.
    """
    global _WORKER_MODEL_PATH, _WORKER_ADAPTER_PATH
    from pathlib import Path

    from huggingface_hub import snapshot_download

    from .identity import IdentityUnavailable, artifact_fingerprints, make_identity

    if get_model.cache_info().currsize:
        raise IdentityUnavailable("model was loaded before identity verification")
    try:
        local = Path(MODEL_NAME).expanduser()
        model_path = (local if local.is_dir() else Path(snapshot_download(
            repo_id=MODEL_NAME,
            local_files_only=os.environ.get("HF_HUB_OFFLINE", "1").strip() != "0",
        ))).resolve(strict=True)
        adapter_path = Path(ADAPTER_PATH).expanduser().resolve(strict=True) if ADAPTER_PATH else None
        before = artifact_fingerprints(model_path, adapter_path)
        implementation_before = encoding_implementation_id()
        _WORKER_MODEL_PATH = str(model_path)
        _WORKER_ADAPTER_PATH = str(adapter_path) if adapter_path else None
        get_model()
        after = artifact_fingerprints(model_path, adapter_path)
        implementation_after = encoding_implementation_id()
        if before != after or implementation_before != implementation_after:
            raise IdentityUnavailable("model artifacts changed during loading")
        return make_identity(after, text_instruction, implementation_after)
    except IdentityUnavailable:
        raise
    except Exception:
        raise IdentityUnavailable("model identity could not be established") from None


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
    return _encode_loaded([Image.open(path).convert("RGB") for path in paths])


def encode_image_bytes(payloads: list[bytes]) -> list[list[float]]:
    """从内存编码图片，不接收调用方给的文件路径。"""
    return _encode_loaded(
        [Image.open(BytesIO(payload)).convert("RGB") for payload in payloads]
    )


def _encode_loaded(images: list[Image.Image]) -> list[list[float]]:
    model = get_model()
    vec = model.encode(
        images,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=len(images) > 8,
    )
    return vec.tolist()


def encoding_implementation_id() -> str:
    """Fingerprint the executable model loading and text/image encoding code."""
    from .identity import implementation_fingerprint

    return implementation_fingerprint({
        "get_model": get_model,
        "pick_device": pick_device,
        "encode_texts": encode_texts,
        "encode_image_bytes": encode_image_bytes,
        "_encode_loaded": _encode_loaded,
    })
