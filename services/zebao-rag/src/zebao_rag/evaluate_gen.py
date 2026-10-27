"""生成侧评估：忠实度 / 引用准确率 / 拒答质量（真实本地模型，可现场演示）。

每条评估问题走「真实检索 → 真实模型生成」两步：
1. 用 tool 层 search_knowledge 取回证据（带文件名），与线上路径同源；
2. 把证据喂给本地模型，要求「只依据证据作答、不足就拒答」；
3. 用可解释的规则判定三件事：
   - 忠实度：答案里的关键事实必须同时出现在证据里（防止编造）；
   - 引用准确率：答案是否如实写出证据里的文件名；
   - 拒答质量：证据不足的问题是否明确拒答，且没编造细节。

只走本机 Ollama 与本地向量库，不联网、不需要密钥。
注意：这是「可解释的代理指标」，不是人工评审；关键样例仍需人工抽查。
"""
import re
from dataclasses import dataclass, field

import httpx

from .config import AGENT_MODEL, AGENT_NUM_CTX, AGENT_TIMEOUT_S, OLLAMA_BASE_URL
from .tools import search_knowledge

TOP_K = 3

# 拒答的合格信号：出现任意一个即视为「明确说没找到」
REFUSAL_MARKERS = ["没有", "未找到", "不包含", "不涉及", "找不到", "无法", "暂无"]

GEN_PROMPT = """你是「开心」，泽宝的本地资料库助手。
下面【证据】是从本地资料库检索到的内容（可能为空）。请只根据证据回答，用中文，简洁直接。

必须遵守：
1. 只依据证据作答，不得补充证据里没有的内容（数字、时间、名称、评价、做法）。
2. 引用到具体资料时，如实写出证据里的文件名。
3. 证据不足以回答时，直接说「库里没有相关内容」，不要猜测或拼凑。"""

# 从证据文本里抽出被引用的文件名（用于判定引用是否来自真实证据）
_SRC_RE = re.compile(r"^\d+\.\s*\[(?:文本|图片)\]\s*(.+?)(?:（|｜|$)", re.M)


@dataclass
class GenCase:
    name: str
    scope: str
    question: str
    kind: str                      # "answer"（应作答）或 "refuse"（应拒答）
    must_contain: list[str] = field(default_factory=list)      # 答案须出现的关键事实
    expect_source: list[str] = field(default_factory=list)     # 答案须引用的来源文件名
    must_not_contain: list[str] = field(default_factory=list)  # 拒答卷里不得出现的编造内容


GEN_CASES: list[GenCase] = [
    # ---- 应作答：忠实度 + 引用 ----
    GenCase("tech 忠实度/引用：RAG 与微调", "tech",
            "RAG 和微调分别适合什么场景？", "answer",
            must_contain=["可更新"], expect_source=["01-RAG基本概念.md"]),
    GenCase("tech 忠实度/引用：分块长短取舍", "tech",
            "文档分块如果太短或太长分别有什么问题？", "answer",
            must_contain=["主题混杂"], expect_source=["02-数据处理与分块.md"]),
    GenCase("personal 忠实度/引用：奖项等级", "personal",
            "我拿过的竞赛奖都是几等奖？", "answer",
            must_contain=["三等奖"], expect_source=["获奖证书.md"]),
    GenCase("personal 忠实度/引用：MathorCup", "personal",
            "MathorCup 那个竞赛我得的是什么奖？", "answer",
            must_contain=["大数据竞赛"], expect_source=["获奖证书.md"]),
    GenCase("japanese 忠实度/引用：五十音 あ", "japanese",
            "日语五十音里的あ怎么记？", "answer",
            must_contain=["ア"], expect_source=["日语五十音快速记忆法.md"]),
    GenCase("japanese 忠实度/引用：五十音 か", "japanese",
            "五十音里 か 的记忆口诀是什么？", "answer",
            must_contain=["卡住"], expect_source=["日语五十音快速记忆法.md"]),

    # ---- 应拒答：证据不足时不得编造 ----
    GenCase("拒答：库外主题（红烧肉做法）", "tech",
            "库里有没有红烧肉的做法？", "refuse",
            must_not_contain=["糖色", "五花肉", "收汁"]),
    GenCase("拒答：库外个人资料（驾驶证）", "personal",
            "我的驾驶证是什么时候考的？", "refuse",
            must_not_contain=["准驾车型", "初次领证", "C1"]),
]


def _generate(question: str, evidence: str) -> str:
    """一次本地模型调用：把证据和问题交给模型，要求只依据证据作答（不带工具）。"""
    payload = {
        "model": AGENT_MODEL,
        "messages": [
            {"role": "system", "content": GEN_PROMPT},
            {"role": "user", "content": f"【证据】\n{evidence}\n\n【问题】{question}"},
        ],
        "stream": False,
        "options": {"num_ctx": AGENT_NUM_CTX},
    }
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=AGENT_TIMEOUT_S
    )
    resp.raise_for_status()
    return (resp.json().get("message", {}).get("content") or "").strip()


def _sources_from_evidence(evidence: str) -> list[str]:
    return _SRC_RE.findall(evidence)


@dataclass
class GenResult:
    case: GenCase
    evidence: str = ""
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    facts_in_answer: bool = False   # 关键事实是否都写进了答案
    facts_grounded: bool = False    # 关键事实是否都能在证据里找到（防编造）
    cited: bool = False             # 是否引用了期望来源
    refused: bool = False           # 拒答卷：是否明确拒答
    clean: bool = False             # 拒答卷：是否没有编造内容
    passed: bool = False
    error: str | None = None        # 模型调用最终失败的原因（超时等）


def _generate_retry(question: str, evidence: str, attempts: int = 2) -> str:
    """有限重试：超时/连接错误最多再试一次，仍失败就抛出，由调用方降级记失败。"""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return _generate(question, evidence)
        except Exception as exc:  # 超时、连接错误等，分类后有限重试
            last = exc
    raise last if last else RuntimeError("模型调用失败")


def _check(case: GenCase, evidence: str, answer: str) -> GenResult:
    r = GenResult(case, evidence=evidence, answer=answer)
    r.sources = _sources_from_evidence(evidence)

    if case.kind == "refuse":
        r.refused = any(m in answer for m in REFUSAL_MARKERS)
        r.clean = not any(t in answer for t in case.must_not_contain)
        r.passed = r.refused and r.clean
    else:
        r.facts_in_answer = all(t in answer for t in case.must_contain)
        # 关键事实必须能在证据里找到，否则视为模型编造
        r.facts_grounded = all(t in evidence for t in case.must_contain)
        r.cited = any(s in answer for s in case.expect_source)
        r.passed = r.facts_in_answer and r.facts_grounded and r.cited
    return r


def run_gen_eval(top_k: int = TOP_K, verbose: bool = True) -> dict:
    """跑生成侧评估集，返回指标字典（同时可打印逐条明细）。"""
    results: list[GenResult] = []
    for case in GEN_CASES:
        evidence = search_knowledge(case.question, scope=case.scope, top_k=top_k)
        try:
            answer = _generate_retry(case.question, evidence)
        except Exception as exc:  # 重试后仍失败：记为该条失败，不中断整轮评估
            r = GenResult(case, evidence=evidence, answer="[模型调用失败]",
                          sources=_sources_from_evidence(evidence),
                          error=f"{type(exc).__name__}: {exc}")
            results.append(r)
            if verbose:
                print(f"  ❌ [{case.scope}] {case.name}｜模型调用失败：{r.error}")
            continue
        r = _check(case, evidence, answer)
        results.append(r)

        if verbose:
            tag = "✅" if r.passed else "❌"
            print(f"  {tag} [{case.scope}] {case.name}")
            print(f"       问：{case.question}")
            print(f"       证据来源：{' / '.join(r.sources) or '（空）'}")
            if case.kind == "refuse":
                print(f"       拒答判定：{'已拒答' if r.refused else '未拒答'}"
                      f"｜无编造：{'是' if r.clean else '否'}")
            else:
                print(f"       事实命中答案：{'是' if r.facts_in_answer else '否'}"
                      f"｜事实有据可查：{'是' if r.facts_grounded else '否'}"
                      f"｜引用了来源：{'是' if r.cited else '否'}")
            print(f"       答：{r.answer.replace(chr(10), ' ')[:160]}")

    ans = [r for r in results if r.case.kind == "answer"]
    ref = [r for r in results if r.case.kind == "refuse"]
    n_ans, n_ref = len(ans), len(ref)

    faithful = sum(1 for r in ans if r.facts_in_answer and r.facts_grounded)
    cited = sum(1 for r in ans if r.cited)
    refused = sum(1 for r in ref if r.passed)

    stats = {
        "n": len(results),
        "answer_n": n_ans,
        "refuse_n": n_ref,
        "faithful": faithful,
        "cited": cited,
        "refused": refused,
    }

    if verbose:
        print("\n" + "=" * 56)
        print(f"生成侧评估汇总（共 {len(results)} 条，真实本地模型）")
        print(f"  忠实度（关键事实有据）   = {faithful}/{n_ans} = "
              f"{faithful / n_ans:.1%}" if n_ans else "  忠实度：无样例")
        print(f"  引用准确率（写出文件名） = {cited}/{n_ans} = "
              f"{cited / n_ans:.1%}" if n_ans else "  引用准确率：无样例")
        print(f"  拒答质量（该拒就拒）     = {refused}/{n_ref} = "
              f"{refused / n_ref:.1%}" if n_ref else "  拒答质量：无样例")
        failed = [r for r in results if not r.passed]
        errors = sum(1 for r in results if r.error)
        if errors:
            print(f"  模型调用失败             = {errors}/{len(results)} 条（已按失败计入分母）")
        if failed:
            print(f"  未通过 {len(failed)} 条：")
            for r in failed:
                print(f"    - [{r.case.scope}] {r.case.name}")
        print("=" * 56)

    stats["results"] = results
    return stats
