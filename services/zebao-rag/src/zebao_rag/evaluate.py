"""检索效果评估：用固定评估集量化 recall@k 与 MRR。

纯离线、只读：只用本机向量模型编码查询，不调用大模型、不联网。
评估集覆盖三个集合（tech / personal / japanese）的文本与图片用例，
每条给「可接受答案集合」——命中集合里任意一个即算命中，
避免用单一文件名把模型判错（例如助眠药有多个正确图片）。
"""
from collections import defaultdict
from dataclasses import dataclass, field

from .config import (
    COLLECTION_NAME,
    JAPANESE_COLLECTION,
    PERSONAL_COLLECTION,
    ROOT,
)
from .embedder import encode_images, encode_texts
from .index import get_collection

# 评估用默认 top-k：一次取 5 条，再分 1/3/5 三档统计
DEFAULT_TOP_K = 5

SCOPE_COLLECTIONS: dict[str, str] = {
    "tech": COLLECTION_NAME,
    "personal": PERSONAL_COLLECTION,
    "japanese": JAPANESE_COLLECTION,
}

# 元数据过滤器别名：文本问答只比文本块，图片问答只比图片，口径清晰
W_TEXT = {"modality": "text"}
W_IMAGE = {"modality": "image"}


@dataclass
class EvalCase:
    name: str
    scope: str
    expect: list[str]           # 可接受答案集合（对 source 做子串匹配）
    query: str | None = None    # 文字查询
    image: str | None = None    # 图片查询（相对仓库根的路径）
    where: dict | None = None   # 元数据过滤（None=不过滤）


EVAL_CASES: list[EvalCase] = [
    # ---- tech：技术笔记（文字搜文）----
    EvalCase("tech 文搜文：RAG 是什么", "tech", ["01-RAG基本概念.md"],
             query="RAG 是什么，它解决什么问题", where=W_TEXT),
    EvalCase("tech 文搜文：分块长短取舍", "tech", ["02-数据处理与分块.md"],
             query="文档分块如果太短或太长会有什么问题", where=W_TEXT),
    EvalCase("tech 文搜文：检索阶段步骤", "tech", ["03-检索与生成流程.md"],
             query="检索阶段要做哪几步", where=W_TEXT),
    EvalCase("tech 文搜文：评估指标含义", "tech",
             ["04-效果评估与迭代.md", "00-学习索引.md"],
             query="Recall@k 和 MRR 分别衡量什么", where=W_TEXT),
    EvalCase("tech 文搜文：保留标题路径", "tech", ["02-数据处理与分块.md"],
             query="分块为什么建议保留标题路径", where=W_TEXT),
    EvalCase("tech 文搜文：相似度≠相关性", "tech", ["03-检索与生成流程.md"],
             query="向量相似度高就代表内容一定相关吗", where=W_TEXT),
    EvalCase("tech 文搜文：RAG 与微调", "tech", ["01-RAG基本概念.md"],
             query="RAG 和微调分别适合什么场景", where=W_TEXT),

    # ---- tech：药品包装图（文字搜图）----
    EvalCase("tech 文搜图：助眠药", "tech",
             ["艾司唑仑", "佐匹克隆", "地达西尼"],
             query="助眠安眠的药物", where=W_IMAGE),
    EvalCase("tech 文搜图：情绪稳定剂", "tech", ["碳酸锂"],
             query="情绪稳定剂 碳酸锂", where=W_IMAGE),
    EvalCase("tech 文搜图：抗焦虑", "tech", ["丁螺环酮"],
             query="抗焦虑 丁螺环酮", where=W_IMAGE),
    EvalCase("tech 文搜图：抗抑郁", "tech", ["地文拉法辛"],
             query="抗抑郁 文拉法辛缓释片", where=W_IMAGE),
    EvalCase("tech 文搜图：抗癫痫/神经痛", "tech", ["克利加巴林"],
             query="苯磺酸克利加巴林胶囊", where=W_IMAGE),
    EvalCase("tech 文搜图：抗精神病", "tech", ["齐拉西酮"],
             query="抗精神病药 盐酸齐拉西酮", where=W_IMAGE),

    # ---- tech：图搜图 ----
    EvalCase("tech 图搜图：碳酸锂片自匹配", "tech", ["碳酸锂"],
             image="data/images/04_碳酸锂缓释片.jpg"),

    # ---- personal：证书文本与证书图片 ----
    EvalCase("personal 文搜文：获奖清单", "personal", ["获奖证书.md"],
             query="我拿过哪些竞赛奖", where=W_TEXT),
    EvalCase("personal 文搜图：MathorCup", "personal", ["MathorCup"],
             query="MathorCup 数学应用挑战赛 大数据竞赛 获奖证书", where=W_IMAGE),
    EvalCase("personal 文搜图：人工智能挑战赛", "personal", ["人工智能挑战赛"],
             query="全国高校计算机能力挑战赛 人工智能挑战赛 证书", where=W_IMAGE),
    EvalCase("personal 文搜图：大数据挑战赛", "personal", ["大数据挑战赛"],
             query="全国高校计算机能力挑战赛 大数据挑战赛 证书", where=W_IMAGE),
    EvalCase("personal 文搜图：身份证", "personal", ["身份证"],
             query="我的身份证证件照", where=W_IMAGE),

    # ---- japanese：日语资料（文字搜文）----
    EvalCase("japanese 文搜文：五十音记忆法", "japanese", ["五十音"],
             query="日语五十音 あ 的记忆方法"),
    EvalCase("japanese 文搜文：基础语法", "japanese", ["日语基础语法完整版"],
             query="日语基础语法完整总结"),
    EvalCase("japanese 文搜文：N5 语法点", "japanese",
             ["N5~N1必背语法条", "N5 新标日"],
             query="N5 阶段要记的语法点"),
    EvalCase("japanese 文搜文：常用口语", "japanese", ["900句常用语"],
             query="常用日语口语 900 句"),
    EvalCase("japanese 文搜文：高频单词", "japanese", ["3500个高频词"],
             query="日语高频单词 3500 个"),
]


@dataclass
class CaseResult:
    case: EvalCase
    sources: list[str] = field(default_factory=list)
    rank: int | None = None     # 第一个命中结果的排名（1 起）；未命中为 None


def _rank(sources: list[str], expect: list[str]) -> int | None:
    for i, s in enumerate(sources, 1):
        if any(e in s for e in expect):
            return i
    return None


def run_eval(top_k: int = DEFAULT_TOP_K, verbose: bool = True) -> dict:
    """跑完整评估集，返回指标字典（同时可打印逐条明细）。"""
    colls: dict[str, object] = {}
    results: list[CaseResult] = []

    for case in EVAL_CASES:
        col = colls.get(case.scope)
        if col is None:
            col = get_collection(SCOPE_COLLECTIONS[case.scope])
            colls[case.scope] = col

        if case.image:
            vec = encode_images([str(ROOT / case.image)])
            shown = f"图片 {case.image}"
        else:
            vec = encode_texts([case.query], is_query=True)
            shown = f"文字「{case.query}」"

        kwargs = {"where": case.where} if case.where else {}
        r = col.query(query_embeddings=vec, n_results=top_k,
                      include=["metadatas"], **kwargs)
        sources = [m["source"] for m in r["metadatas"][0]]
        rank = _rank(sources, case.expect)
        results.append(CaseResult(case, sources, rank))

        if verbose:
            top1 = sources[0] if sources else "（空）"
            print(f"  {'✅' if rank else '❌'} [{case.scope}] {case.name}"
                  f"（排名 {rank if rank else '未命中'}）")
            print(f"       输入：{shown}")
            print(f"       期望：{case.expect}")
            if rank:
                print(f"       Top1：{top1}")
            else:
                print(f"       Top{top_k}：{' / '.join(sources)}")

    n = len(results)
    hit1 = sum(1 for r in results if r.rank == 1)
    hit3 = sum(1 for r in results if r.rank and r.rank <= 3)
    hitk = sum(1 for r in results if r.rank)
    mrr = sum(1.0 / r.rank for r in results if r.rank) / n if n else 0.0

    # 分集合统计，便于看出是哪一块拖后腿
    per_scope: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        per_scope[r.case.scope].append(r)
    scope_stats = {
        s: {
            "n": len(rs),
            "hit1": sum(1 for r in rs if r.rank == 1),
            "hit3": sum(1 for r in rs if r.rank and r.rank <= 3),
        }
        for s, rs in per_scope.items()
    }

    stats = {
        "n": n,
        "top_k": top_k,
        "hit1": hit1,
        "hit3": hit3,
        f"hit{top_k}": hitk,
        "mrr": mrr,
        "scope": scope_stats,
    }

    if verbose:
        print("\n" + "=" * 56)
        print(f"评估汇总（共 {n} 条，top_k={top_k}）")
        print(f"  Recall@1 = {hit1}/{n} = {hit1 / n:.1%}")
        print(f"  Recall@3 = {hit3}/{n} = {hit3 / n:.1%}")
        print(f"  Recall@{top_k} = {hitk}/{n} = {hitk / n:.1%}")
        print(f"  MRR      = {mrr:.3f}")
        for s, st in sorted(scope_stats.items()):
            print(f"    [{s}] {st['n']} 条：Recall@1 {st['hit1']}/{st['n']}"
                  f"，Recall@3 {st['hit3']}/{st['n']}")
        missed = [(r.case.scope, r.case.name, r.case.expect) for r in results if not r.rank]
        if missed:
            print(f"  未命中 {len(missed)} 条：")
            for scope, name, exp in missed:
                print(f"    - [{scope}] {name}（期望 {exp}）")
        print("=" * 56)

    stats["results"] = results
    return stats
