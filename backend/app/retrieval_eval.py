"""Offline, evidence-level evaluation using the application's lexical baseline."""

import hashlib
import platform
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import knowledge

Category = Literal["direct", "paraphrase", "negative", "long_document", "scoped"]
CATEGORY_LABELS = {
    "direct": "直接提问",
    "paraphrase": "不同表述",
    "negative": "无答案",
    "long_document": "长文",
    "scoped": "指定文档",
}
FAILURE_LABELS = {
    "wrong_top1": "第一条未包含标注证据",
    "missing_evidence": "前五条未召回全部标注证据",
    "false_positive": "无答案问题仍返回片段",
    "evidence_not_in_chunks": "标注证据未完整保留在任一分块中",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    filename: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self) -> Evidence:
        if self.line_end < self.line_start or not self.quote.strip():
            raise ValueError("证据行号必须有序，引用不能只有空白。")
        return self


class EvalCase(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    category: Category
    query: str = Field(min_length=1, max_length=1000)
    scope: str | None = None
    expected: list[Evidence]

    @model_validator(mode="after")
    def nonblank_query(self) -> EvalCase:
        if not self.query.strip():
            raise ValueError("评估问题不能为空白。")
        return self


class Dataset(StrictModel):
    schema_version: Literal[2]
    dataset_id: str = Field(min_length=1)
    description: str
    corpus: list[str] = Field(min_length=1)
    cases: list[EvalCase] = Field(min_length=1)


@dataclass(frozen=True)
class CorpusChunk:
    filename: str
    position: int
    heading: str
    line_start: int
    line_end: int
    content: str

    @property
    def key(self) -> str:
        return f"{self.filename}#{self.position + 1}"

    def matches(self, evidence: Evidence) -> bool:
        return (
            self.filename == evidence.filename
            and self.line_start <= evidence.line_start
            and self.line_end >= evidence.line_end
            and evidence.quote in self.content
        )


class Retrieved(StrictModel):
    rank: int
    key: str
    filename: str
    heading: str
    line_start: int
    line_end: int
    content: str
    score: float
    evidence_ids: list[int]


class CaseResult(StrictModel):
    case: EvalCase
    sources: list[Retrieved]
    top1_hit: bool | None
    recall_at_5: float | None
    reciprocal_rank_at_5: float | None
    no_hit_correct: bool | None
    passed: bool
    failures: list[str]


class Metrics(StrictModel):
    case_count: int
    answerable_count: int
    unanswerable_count: int
    passed_count: int
    top1_hit_rate: float | None
    recall_at_5: float | None
    mrr_at_5: float | None
    no_hit_accuracy: float | None
    false_positive_rate: float | None


class EvalReport(StrictModel):
    schema_version: Literal[2] = 2
    method: str = "bm25"
    dataset_id: str
    dataset_description: str
    dataset_sha256: str
    corpus_sha256: dict[str, str]
    retrieval_code_sha256: str
    python_version: str
    chunk_count: int
    parameters: dict[str, int]
    real_model_attempted: bool = False
    database_accessed: bool = False
    metrics: Metrics
    by_category: dict[str, Metrics]
    cases: list[CaseResult]


def load_dataset(
    examples_dir: Path, dataset_path: Path
) -> tuple[Dataset, list[CorpusChunk], dict[str, str]]:
    dataset = Dataset.model_validate_json(dataset_path.read_bytes())
    root = examples_dir.resolve()
    chunks: list[CorpusChunk] = []
    originals: dict[str, list[str]] = {}
    hashes: dict[str, str] = {}
    for relative in dataset.corpus:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"语料路径无效或超出 examples 目录：{relative}")
        raw = path.read_bytes()
        filename, text, _ = knowledge.parse_document(path.name, raw)
        if filename in originals:
            raise ValueError(f"语料文件名重复，不能唯一定位证据：{filename}")
        originals[filename] = text.splitlines()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        chunks.extend(
            CorpusChunk(
                filename,
                position,
                chunk.heading,
                chunk.line_start,
                chunk.line_end,
                chunk.content,
            )
            for position, chunk in enumerate(knowledge.split_document(text))
        )
    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("评估样例 ID 重复。")
    for case in dataset.cases:
        if case.scope is not None and case.scope not in originals:
            raise ValueError(f"{case.id} 指定了不存在的文档：{case.scope}")
        for evidence in case.expected:
            lines = originals.get(evidence.filename)
            if lines is None or evidence.line_end > len(lines):
                raise ValueError(f"{case.id} 的证据文件或行号不存在。")
            if case.scope is not None and case.scope != evidence.filename:
                raise ValueError(f"{case.id} 的证据不在指定文档内。")
            span = "\n".join(lines[evidence.line_start - 1 : evidence.line_end])
            if evidence.quote not in span:
                raise ValueError(f"{case.id} 的标注引用不在指定原文行内。")
        keys = [(e.filename, e.line_start, e.line_end, e.quote) for e in case.expected]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{case.id} 的证据标注重复。")
    return dataset, chunks, hashes


def evaluate_case(case: EvalCase, corpus: Sequence[CorpusChunk]) -> CaseResult:
    candidates = [
        chunk for chunk in corpus if case.scope is None or chunk.filename == case.scope
    ]
    ranked = knowledge.rank_chunks(
        case.query.strip(),
        [f"{c.filename}\n{c.heading}\n{c.content}" for c in candidates],
        limit=5,
    )
    sources = []
    for rank, (index, score) in enumerate(ranked, 1):
        chunk = candidates[index]
        sources.append(
            Retrieved(
                rank=rank,
                key=chunk.key,
                filename=chunk.filename,
                heading=chunk.heading,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                content=chunk.content,
                score=round(score, 6),
                evidence_ids=[
                    i for i, gold in enumerate(case.expected, 1) if chunk.matches(gold)
                ],
            )
        )
    if not case.expected:
        correct = not sources
        return CaseResult(
            case=case,
            sources=sources,
            top1_hit=None,
            recall_at_5=None,
            reciprocal_rank_at_5=None,
            no_hit_correct=correct,
            passed=correct,
            failures=[] if correct else ["false_positive"],
        )
    hit_ids = {number for source in sources for number in source.evidence_ids}
    top1 = bool(sources and sources[0].evidence_ids)
    recall = len(hit_ids) / len(case.expected)
    first = next((source.rank for source in sources if source.evidence_ids), None)
    failures = []
    if not top1:
        failures.append("wrong_top1")
    if recall < 1:
        failures.append("missing_evidence")
    if any(
        not any(chunk.matches(gold) for chunk in candidates) for gold in case.expected
    ):
        failures.append("evidence_not_in_chunks")
    return CaseResult(
        case=case,
        sources=sources,
        top1_hit=top1,
        recall_at_5=recall,
        reciprocal_rank_at_5=1 / first if first else 0,
        no_hit_correct=None,
        passed=top1 and recall == 1,
        failures=failures,
    )


def aggregate(results: Sequence[CaseResult]) -> Metrics:
    positive = [r for r in results if r.case.expected]
    negative = [r for r in results if not r.case.expected]
    no_hit = sum(bool(r.no_hit_correct) for r in negative)
    return Metrics(
        case_count=len(results),
        answerable_count=len(positive),
        unanswerable_count=len(negative),
        passed_count=sum(r.passed for r in results),
        top1_hit_rate=sum(bool(r.top1_hit) for r in positive) / len(positive)
        if positive
        else None,
        recall_at_5=sum(r.recall_at_5 or 0 for r in positive) / len(positive)
        if positive
        else None,
        mrr_at_5=sum(r.reciprocal_rank_at_5 or 0 for r in positive) / len(positive)
        if positive
        else None,
        no_hit_accuracy=no_hit / len(negative) if negative else None,
        false_positive_rate=(len(negative) - no_hit) / len(negative)
        if negative
        else None,
    )


def evaluate(examples_dir: Path, dataset_path: Path) -> EvalReport:
    dataset, corpus, hashes = load_dataset(examples_dir, dataset_path)
    results = [evaluate_case(case, corpus) for case in dataset.cases]
    categories = sorted({case.category for case in dataset.cases})
    return EvalReport(
        dataset_id=dataset.dataset_id,
        dataset_description=dataset.description,
        dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        corpus_sha256=hashes,
        retrieval_code_sha256=hashlib.sha256(
            Path(knowledge.__file__).read_bytes()
        ).hexdigest(),
        python_version=platform.python_version(),
        chunk_count=len(corpus),
        parameters={
            "chunk_characters": knowledge.CHUNK_CHARACTERS,
            "chunk_overlap": knowledge.CHUNK_OVERLAP,
            "top_k": 5,
        },
        metrics=aggregate(results),
        cases=results,
        by_category={
            category: aggregate([r for r in results if r.case.category == category])
            for category in categories
        },
    )


def percentage(value: float | None) -> str:
    return "不适用" if value is None else f"{value:.1%}"


def table_text(value: str) -> str:
    return (
        value.replace("|", "\\|")
        .replace("\n", " ")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def code_block(value: str) -> str:
    fence = "`" * max(3, max((len(m) + 1 for m in re.findall(r"`+", value)), default=0))
    return f"{fence}text\n{value}\n{fence}"


def render_markdown(report: EvalReport) -> str:
    metrics = report.metrics
    lines = [
        "# DevAssist 片段级检索评估报告",
        "",
        report.dataset_description,
        "",
        f"数据集：`{report.dataset_id}`；{len(report.corpus_sha256)} 份合成文档，{report.chunk_count} 个分块，{metrics.case_count} 个问题。",
        "离线复用应用的解析、分块和 BM25 排序函数；没有连接数据库、读取模型配置或调用模型。",
        "",
        "## 指标与判定口径",
        "",
        "命中须同时满足文件名正确、片段覆盖标注行号、片段含有标注引用。仅命中文件名不计成功。",
        "标注依据原文独立编写；多条证据按证据条目计算召回，同一个重叠片段的重复命中不重复计数。",
        "",
        "| 指标 | 结果 | 分母与定义 |",
        "|---|---|---|",
        f"| Top-1 命中率 | {percentage(metrics.top1_hit_rate)} | {metrics.answerable_count} 个有答案问题，第一条包含至少一条标注证据 |",
        f"| Recall@5 | {percentage(metrics.recall_at_5)} | 每个有答案问题前五条召回的证据比例，再对问题取平均 |",
        f"| MRR@5 | {'不适用' if metrics.mrr_at_5 is None else f'{metrics.mrr_at_5:.3f}'} | 第一条正确证据排名的倒数；前五条无正确证据记 0 |",
        f"| 无答案返回空结果比例 | {percentage(metrics.no_hit_accuracy)} | {metrics.unanswerable_count} 个无答案问题中没有返回片段的比例 |",
        f"| 无答案返回片段比例 | {percentage(metrics.false_positive_rate)} | 无答案问题仍返回片段的比例 |",
        "",
        "无答案表示语料没有问题所需的操作证据；返回的片段可能主题相关，但不足以给出该问题的具体答案。这项指标不代表模型已经生成了错误回答。",
        "样例严格通过条件：有答案问题的首条正确且全部证据在前五条；无答案问题返回空结果。",
        "",
        f"严格通过 {metrics.passed_count}/{metrics.case_count}；失败样例如实保留。",
        "",
        "## 分类结果",
        "",
        "| 类别 | 问题数（有答案/无答案） | Top-1 | Recall@5 | 无答案返回片段 |",
        "|---|---|---|---|---|",
    ]
    for category, group in report.by_category.items():
        lines.append(
            f"| {CATEGORY_LABELS[category]} | {group.case_count}（{group.answerable_count}/{group.unanswerable_count}） | {percentage(group.top1_hit_rate)} | {percentage(group.recall_at_5)} | {percentage(group.false_positive_rate)} |"
        )
    lines.extend(
        [
            "",
            "## 全部样例",
            "",
            "| ID | 类别 | 范围 | 问题 | 标注证据行号 | 首条（行号） | 结果 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for result in report.cases:
        case = result.case
        first = (
            f"{result.sources[0].key}（{result.sources[0].line_start}～{result.sources[0].line_end}）"
            if result.sources
            else "无结果"
        )
        expected = (
            "；".join(
                f"{gold.filename}:{gold.line_start}～{gold.line_end}"
                for gold in case.expected
            )
            or "无操作证据"
        )
        lines.append(
            f"| {case.id} | {CATEGORY_LABELS[case.category]} | {case.scope or '全部文档'} | {table_text(case.query)} | {table_text(expected)} | {table_text(first)} | {'通过' if result.passed else '失败'} |"
        )
    lines.extend(["", "## 失败样例与证据", ""])
    for result in report.cases:
        if result.passed:
            continue
        case = result.case
        lines.extend(
            [
                f"### {case.id} · {CATEGORY_LABELS[case.category]}",
                "",
                f"问题：{table_text(case.query)}；范围：{case.scope or '全部文档'}。",
                "",
                "原因："
                + "；".join(FAILURE_LABELS[reason] for reason in result.failures)
                + "。",
                "",
            ]
        )
        if not case.expected:
            lines.extend(["标注：原文没有此问题所需的操作证据。", ""])
        for number, evidence in enumerate(case.expected, 1):
            lines.extend(
                [
                    f"标注 {number}：{evidence.filename}，第 {evidence.line_start}～{evidence.line_end} 行。",
                    "",
                    code_block(evidence.quote),
                    "",
                ]
            )
        lines.extend(
            [
                "| 排名 | 返回片段 | 行号 | BM25 分数 | 命中证据编号 |",
                "|---|---|---|---|---|",
            ]
        )
        for source in result.sources:
            lines.append(
                f"| {source.rank} | {table_text(source.key)} | {source.line_start}～{source.line_end} | {source.score:.6f} | {source.evidence_ids or '无'} |"
            )
        if result.sources:
            lines.extend(
                [
                    "",
                    "首条原文摘录（最多 240 字符；JSON 保存完整片段）：",
                    "",
                    code_block(result.sources[0].content[:240]),
                    "",
                ]
            )
        else:
            lines.extend(["| — | 无结果 | — | — | — |", ""])
    lines.extend(
        [
            "## 可复现信息与边界",
            "",
            f"- 数据集 SHA-256：`{report.dataset_sha256}`。",
            f"- 检索代码 SHA-256：`{report.retrieval_code_sha256}`。",
            f"- Python：`{report.python_version}`；分块上限 {report.parameters['chunk_characters']} 字符，重叠 {report.parameters['chunk_overlap']} 字符，Top-K=5。",
            "- 语料按数据集声明的顺序加载，分数相同时沿用该顺序；线上数据库的文档顺序可能不同。",
            "- 指定文档样例先在离线语料中过滤范围。线上权限隔离另由 API 回归验证。",
            "- 这是小规模合成诊断集，未划分训练/验证集，不能据此声称真实项目检索质量、模型回答质量或生产性能。",
            "",
            "| 语料路径 | SHA-256 |",
            "|---|---|",
        ]
    )
    for path, digest in report.corpus_sha256.items():
        lines.append(f"| {table_text(path)} | `{digest}` |")
    return "\n".join(lines) + "\n"
