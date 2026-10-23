"""建库：把笔记文本块和图片编码后写入同一个 Chroma 集合。

- 自己用多模态模型算好向量传入（不用 Chroma 默认文本 embedder），
  这样图片和文本才能在同一向量空间里检索。
- 支持重复执行：每次重建前清空旧集合，避免新旧版本重复入库。
"""
import chromadb

from pathlib import Path

from .chunking import load_chunks
from .config import CHROMA_DIR, COLLECTION_NAME, DOCS_DIR, EMBED_DIM, IMAGES_DIR
from .embedder import encode_images, encode_texts


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def build(
    collection_name: str = COLLECTION_NAME,
    docs_dir: Path = DOCS_DIR,
    images_dir: Path = IMAGES_DIR,
) -> dict:
    client = _client()
    # 删旧建新，保证可重复执行、无重复数据
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    col = client.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # ---- 文本块 ----
    chunks = load_chunks(docs_dir)
    stats = {"text_chunks": 0, "images": 0}
    if chunks:
        t_emb = encode_texts([c.text for c in chunks])
        col.add(
            ids=[f"doc::{c.source}::{c.index}" for c in chunks],
            embeddings=t_emb,
            documents=[c.text for c in chunks],
            metadatas=[
                {"modality": "text", "source": c.source, "heading": c.heading}
                for c in chunks
            ],
        )
        stats["text_chunks"] = len(chunks)

    # ---- 图片 ----
    img_paths = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.png"))
    if img_paths:
        i_emb = encode_images([str(p) for p in img_paths])
        col.add(
            ids=[f"img::{p.name}" for p in img_paths],
            embeddings=i_emb,
            documents=[p.name for p in img_paths],  # 存文件名作为可读标签
            metadatas=[
                {"modality": "image", "source": p.name, "path": str(p)}
                for p in img_paths
            ],
        )
        stats["images"] = len(img_paths)

    count = col.count()
    print(f"建库完成({collection_name})：文本块 {stats['text_chunks']}，图片 {stats['images']}，"
          f"集合总数 {count}，维度 {EMBED_DIM}")
    print(f"持久化目录：{CHROMA_DIR}")
    return stats


def get_collection(name: str = COLLECTION_NAME):
    return _client().get_collection(name)
