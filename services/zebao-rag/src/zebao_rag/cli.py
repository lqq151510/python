"""命令行入口：

  uv run python -m zebao_rag.cli build                # 建库（笔记文本+图片）
  uv run python -m zebao_rag.cli verify               # 图文同空间自检（文字搜图片）
  uv run python -m zebao_rag.cli search "查询"          # 文字跨模态检索
  uv run python -m zebao_rag.cli image 图片路径         # 以图搜图/搜文
  uv run python -m zebao_rag.cli build-personal       # 建个人集合（证书等）
  uv run python -m zebao_rag.cli search-personal "查询" # 在个人集合里检索
  uv run python -m zebao_rag.cli build-japanese       # 建日语资料集合
  uv run python -m zebao_rag.cli search-japanese "查询" # 在日语集合里检索
  uv run python -m zebao_rag.cli demo                 # 一键演示 3 个固定案例
  uv run python -m zebao_rag.cli ask "问题"            # 让本地 Agent 自己查库回答（单轮）
  uv run python -m zebao_rag.cli chat                 # 多轮对话：历史落盘，可续聊
  uv run python -m zebao_rag.cli chat 日语 --new       # 指定会话名 / 开新会话
  uv run python -m zebao_rag.cli sessions             # 列出已存档的会话
  uv run python -m zebao_rag.cli eval                 # 检索效果评估（recall@k / MRR）
  uv run python -m zebao_rag.cli eval-gen             # 生成侧评估（忠实度/引用/拒答）
"""
import sys

from .agent import ChatSession, ask as agent_ask
from .codegen import CodeGenerationError, generate_code
from .config import (
    AGENT_DEFAULT_SESSION,
    IMAGES_DIR,
    JAPANESE_COLLECTION,
    JAPANESE_DOCS_DIR,
    JAPANESE_IMAGES_DIR,
    PERSONAL_COLLECTION,
    PERSONAL_DOCS_DIR,
    PERSONAL_IMAGES_DIR,
)
from .embedder import encode_images, encode_texts
from .evaluate import run_eval
from .evaluate_gen import run_gen_eval
from .index import build, get_collection
from .session import SessionStore


def _print_hits(results: dict, top_k: int) -> None:
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for i in range(min(top_k, len(docs))):
        m = metas[i]
        score = 1 - dists[i]  # cosine 距离转相似度
        kind = "🖼 图片" if m.get("modality") == "image" else "📄 文本"
        title = m.get("source", "?")
        preview = docs[i].replace("\n", " ")
        if m.get("modality") != "image" and len(preview) > 80:
            preview = preview[:80] + "…"
        print(f"  {i+1}. {kind} | 相似度 {score:.3f} | {title}")
        if m.get("modality") != "image":
            print(f"       {preview}")


def cmd_build() -> None:
    build()


def cmd_build_personal() -> None:
    """建个人集合：证书文本 + 证书图片，独立于技术笔记集合。"""
    build(PERSONAL_COLLECTION, PERSONAL_DOCS_DIR, PERSONAL_IMAGES_DIR)


def cmd_build_japanese() -> None:
    """建日语集合：只入库文本（本轮没有配图，故传空图片目录）。"""
    JAPANESE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    build(JAPANESE_COLLECTION, JAPANESE_DOCS_DIR, JAPANESE_IMAGES_DIR)


def _search(col, query: str, top_k: int) -> None:
    vec = encode_texts([query], is_query=True)
    results = col.query(query_embeddings=vec, n_results=top_k,
                        include=["documents", "metadatas", "distances"])
    print(f"查询：「{query}」\n跨模态 Top{top_k}：")
    _print_hits(results, top_k)


def cmd_search(query: str, top_k: int = 5) -> None:
    _search(get_collection(), query, top_k)


def cmd_search_personal(query: str, top_k: int = 5) -> None:
    _search(get_collection(PERSONAL_COLLECTION), query, top_k)


def cmd_search_japanese(query: str, top_k: int = 5) -> None:
    _search(get_collection(JAPANESE_COLLECTION), query, top_k)


def cmd_image(image_path: str, top_k: int = 5) -> None:
    col = get_collection()
    vec = encode_images([image_path])
    results = col.query(query_embeddings=vec, n_results=top_k,
                        include=["documents", "metadatas", "distances"])
    print(f"以图检索：{image_path}\nTop{top_k}：")
    _print_hits(results, top_k)


def cmd_verify() -> None:
    """文字搜图片，检验图文是否在同一空间。

    助眠类查询把多种安眠药都视为正确（艾司唑仑片/佐匹克隆片/地达西尼胶囊都是安眠药），
    用可接受答案集合判定，避免用单一文件名误判模型。
    """
    col = get_collection()
    cases: dict[str, list[str]] = {
        "碳酸锂 情绪稳定剂": ["碳酸锂缓释片"],
        "助眠安眠的药物": ["佐匹克隆片", "艾司唑仑片", "地达西尼胶囊"],
        "抗焦虑 丁螺环酮": ["盐酸丁螺环酮片"],
        "抗抑郁 文拉法辛": ["琥珀酸地文拉法辛缓释片"],
        "苯磺酸克利加巴林": ["苯磺酸克利加巴林胶囊"],
    }
    hit = 0
    for q, accepts in cases.items():
        vec = encode_texts([q], is_query=True)
        r = col.query(query_embeddings=vec, n_results=10,
                      where={"modality": "image"},
                      include=["metadatas", "distances"])
        sources = [m["source"] for m in r["metadatas"][0]]
        top = sources[0]
        ok = any(a in top for a in accepts)
        hit += ok
        print(f"  {'✅' if ok else '❌'} 「{q}」-> {top}")
    print(f"\n图文混检命中 {hit}/{len(cases)}",
          "—— 验证通过 ✅" if hit >= len(cases) - 1 else "—— 未达标 ❌")


# 固定演示案例：覆盖 文搜图 / 文搜文 / 图搜图 三个跨模态方向。
# expect 是“可接受答案”集合，避免用单一文件名误判模型。
DEMO_CASES: list[dict] = [
    {
        "name": "文搜图：按用途找药",
        "query": "助眠安眠的药物",
        "where": {"modality": "image"},
        "expect": ["佐匹克隆片", "艾司唑仑片", "地达西尼胶囊"],
    },
    {
        "name": "文搜文：在技术笔记里找答案",
        "query": "文档为什么要分块",
        "where": {"modality": "text"},
        "expect": ["02-数据处理与分块.md"],
    },
    {
        "name": "图搜图：拿一张图找相似内容",
        "image": "04_碳酸锂缓释片.jpg",
        "where": None,
        "expect": ["04_碳酸锂缓释片.jpg"],
    },
]


def cmd_demo() -> None:
    """一键演示：跑 3 个固定案例并逐条判定命中，用于现场演示和回归自检。

    只使用技术笔记集合（zebao_multimodal），不触碰个人集合，
    因此本命令的输出可以对外展示。
    """
    col = get_collection()
    passed = 0
    print("=" * 60)
    print("zebao-rag 演示：多模态检索 3 个固定案例")
    print("=" * 60)
    for i, case in enumerate(DEMO_CASES, 1):
        if "image" in case:
            vec = encode_images([str(IMAGES_DIR / case["image"])])
            shown = f"图片 {case['image']}"
        else:
            vec = encode_texts([case["query"]], is_query=True)
            shown = f"文字「{case['query']}」"
        kwargs = {"where": case["where"]} if case["where"] else {}
        r = col.query(query_embeddings=vec, n_results=3,
                      include=["metadatas", "distances"], **kwargs)
        metas, dists = r["metadatas"][0], r["distances"][0]
        top = metas[0]["source"] if metas else "（空）"
        ok = any(e in top for e in case["expect"])
        passed += ok
        print(f"\n[案例 {i}] {case['name']}")
        print(f"  输入：{shown}")
        for m, d in zip(metas, dists):
            kind = "🖼 图片" if m.get("modality") == "image" else "📄 文本"
            print(f"    {kind} | 相似度 {1 - d:.3f} | {m['source']}")
        print(f"  结果：{'✅ 命中' if ok else '❌ 未命中'}（期望 {case['expect']}）")
    print(f"\n汇总：{passed}/{len(DEMO_CASES)} 通过")


def cmd_ask(question: str) -> None:
    """让本地 Agent 自主决定要不要查库、查哪个库，再回答。"""
    print(f"问：{question}\n")
    print(f"答：{agent_ask(question)}")


def cmd_code(task: str) -> None:
    """Generate code with the configured Ollama coding model."""
    try:
        answer = generate_code(task)
    except CodeGenerationError as exc:
        print(f"代码生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(answer)


def cmd_chat() -> None:
    """交互式多轮对话：同一个 session 里带上历史，直到 exit/退出。"""
    print("多轮对话（带历史记忆）｜输入 exit 或 退出 结束\n")
    session = ChatSession()
    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return
        if not question:
            continue
        if question in {"exit", "quit", "q", "退出"}:
            print("已退出。")
            return
        print(f"\n开心 > {session.turn(question)}\n")


def cmd_sessions() -> None:
    """列出已存档的会话，便于确认落盘生效。"""
    rows = SessionStore.list_sessions()
    if not rows:
        print("还没有任何存档会话。")
        return
    print(f"共 {len(rows)} 个会话（按更新时间倒序）：")
    for r in rows:
        print(
            f"  {r['name']}｜{r['turns']} 轮｜摘要 {len(r['summary'])} 字"
            f"｜更新 {r['updated_at']}"
        )


def cmd_eval() -> None:
    """检索效果评估：跑固定评估集，量化 recall@k 与 MRR（只读、不调大模型）。"""
    print("检索效果评估（纯向量检索，不调用大模型）\n")
    run_eval()


def cmd_eval_gen() -> None:
    """生成侧评估：真实检索 + 真实模型生成，量化忠实度/引用准确率/拒答质量。"""
    print("生成侧评估（真实本地模型：忠实度 / 引用准确率 / 拒答质量）\n")
    run_gen_eval()


def cmd_chat(session_id: str = AGENT_DEFAULT_SESSION, fresh: bool = False) -> None:
    """交互式多轮对话：历史落盘，退出后可用同一会话名接着聊。"""
    session = ChatSession(session_id=session_id)
    if fresh:
        session.reset()

    name = session.store.name if session.store else "（不落盘）"
    print(f"多轮对话｜会话「{name}」")
    if session.resumed:
        extra = f" + 摘要 {len(session.summary)} 字" if session.summary else ""
        print(f"已载入 {session.turn_count} 轮历史（原文剩 {session.turns} 轮）{extra}")
        if session.summary:
            print(f"前文摘要：{session.summary}")
    if session.store:
        print(f"存档文件：{session.store.path}")
    print("命令：exit 退出｜/clear 清空本会话｜/summary 查看摘要\n")

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出（历史已存档）。")
            return
        if not question:
            continue
        if question in {"exit", "quit", "q", "退出"}:
            print("已退出（历史已存档）。")
            return
        if question == "/clear":
            session.reset()
            print("本会话已清空。\n")
            continue
        if question == "/summary":
            print(f"\n摘要（{len(session.summary)} 字）：{session.summary or '（还没压缩过）'}\n")
            continue
        print(f"\n开心 > {session.turn(question)}\n")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]
    if cmd == "build":
        cmd_build()
    elif cmd == "build-personal":
        cmd_build_personal()
    elif cmd == "build-japanese":
        cmd_build_japanese()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "demo":
        cmd_demo()
    elif cmd == "search" and rest:
        cmd_search(rest[0])
    elif cmd == "search-personal" and rest:
        cmd_search_personal(rest[0])
    elif cmd == "search-japanese" and rest:
        cmd_search_japanese(rest[0])
    elif cmd == "ask" and rest:
        cmd_ask(rest[0])
    elif cmd == "code" and rest:
        cmd_code(" ".join(rest))
    elif cmd == "chat":
        name = next((a for a in rest if not a.startswith("--")), AGENT_DEFAULT_SESSION)
        cmd_chat(name, fresh="--new" in rest)
    elif cmd == "sessions":
        cmd_sessions()
    elif cmd == "eval":
        cmd_eval()
    elif cmd == "eval-gen":
        cmd_eval_gen()
    elif cmd == "image" and rest:
        cmd_image(rest[0])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
