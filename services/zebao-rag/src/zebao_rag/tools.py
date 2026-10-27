"""把向量库检索封装成模型可调用的工具（function calling）。

只暴露一个只读工具 search_knowledge：Agent 能主动查库，但查不出、改不了、
删不掉任何东西 —— 工具层权限最小化。
"""
from .config import (
    COLLECTION_NAME,
    JAPANESE_COLLECTION,
    PERSONAL_COLLECTION,
)
from .embedder import encode_texts
from .index import get_collection

# scope 名字 -> 实际集合名。三个集合互相隔离，Agent 只能按名字选，不能越界。
SCOPES: dict[str, str] = {
    "tech": COLLECTION_NAME,          # 技术笔记 + 药品包装图
    "personal": PERSONAL_COLLECTION,  # 证书、证件等个人资料
    "japanese": JAPANESE_COLLECTION,  # 日语学习资料
}

# 给模型看的工具定义（OpenAI / Ollama 通用格式）
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "在本地私有资料库里做跨模态检索（文字搜文字、文字搜图片）。"
                "可查技术笔记、药品包装图、个人获奖证书、日语学习资料。只读，不会修改任何数据。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "中文检索问题或关键词，例如「文档为什么要分块」",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["tech", "personal", "japanese"],
                        "description": (
                            "检索范围。tech=技术笔记与药品图（默认）；"
                            "personal=个人证书与证件；japanese=日语学习资料"
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 3，最多 5",
                    },
                },
                "required": ["query"],
            },
        },
    }
]


def search_knowledge(query: str, scope: str = "tech", top_k: int = 3) -> str:
    """按 scope 路由到对应集合检索，返回给模型阅读的纯文本结果。"""
    if scope not in SCOPES:
        return f"scope 不合法：{scope}，可选 {sorted(SCOPES)}"
    k = max(1, min(int(top_k or 3), 5))
    col = get_collection(SCOPES[scope])
    vec = encode_texts([query], is_query=True)
    r = col.query(
        query_embeddings=vec,
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    docs = r["documents"][0]
    metas = r["metadatas"][0]
    dists = r["distances"][0]
    if not docs:
        return f"「{query}」在 {scope} 范围内没有命中任何内容。"

    lines = [f"「{query}」（范围 {scope}）Top{len(docs)}："]
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        score = 1 - dist  # cosine 距离 -> 相似度
        if meta.get("modality") == "image":
            lines.append(f"{i}. [图片] {meta['source']}｜相似度 {score:.3f}")
        else:
            heading = meta.get("heading") or ""
            lines.append(
                f"{i}. [文本] {meta['source']}"
                f"{f'（{heading}）' if heading else ''}｜相似度 {score:.3f}"
            )
            lines.append(f"   内容：{doc.replace(chr(10), ' ')[:200]}")
    return "\n".join(lines)


# 工具名 -> 实现。Agent 只认这张表，不做动态导入。
TOOL_IMPL = {"search_knowledge": search_knowledge}
