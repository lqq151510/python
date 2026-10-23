"""Generate offline evidence-level reports and optional regression gates."""

import argparse
import json
import sys
from pathlib import Path

from app.retrieval_eval import evaluate, render_markdown


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="离线片段级 BM25 检索评估，不需要模型密钥或数据库。"
    )
    parser.add_argument(
        "--dataset", type=Path, default=root / "examples/retrieval-eval.json"
    )
    parser.add_argument(
        "--json-output", type=Path, default=root / ".local/retrieval-eval.json"
    )
    parser.add_argument(
        "--report", type=Path, default=root / "docs/RETRIEVAL_EVAL_REPORT.md"
    )
    parser.add_argument(
        "--strict", action="store_true", help="生成报告后，存在失败样例时返回退出码 1。"
    )
    parser.add_argument(
        "--gate",
        type=Path,
        help="按固定 JSON 门槛检查指标，适用于已知存在失败样例的诊断集。",
    )
    args = parser.parse_args()
    try:
        report = evaluate(root / "examples", args.dataset)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"评估数据无效：{exc}\n")
    gate_failures: list[str] = []
    if args.gate:
        try:
            gate = json.loads(args.gate.read_text(encoding="utf-8"))
            if gate.get("dataset_id") != report.dataset_id:
                raise ValueError("门槛文件 dataset_id 与评估数据集不匹配。")
            minimums = gate.get("minimums", {})
            maximums = gate.get("maximums", {})
            allowed = {
                "passed_count",
                "top1_hit_rate",
                "recall_at_5",
                "mrr_at_5",
                "no_hit_accuracy",
                "false_positive_rate",
            }
            if (
                not isinstance(minimums, dict)
                or not isinstance(maximums, dict)
                or not minimums
                or not set(minimums) <= allowed
                or not set(maximums) <= allowed
            ):
                raise ValueError("门槛指标缺失、未知或格式无效。")
            metrics = report.metrics.model_dump()
            for direction, limits, comparator in (
                ("minimum", minimums, lambda value, limit: value >= limit),
                ("maximum", maximums, lambda value, limit: value <= limit),
            ):
                for name, limit in limits.items():
                    value = metrics[name]
                    if (
                        isinstance(limit, bool)
                        or not isinstance(limit, (int, float))
                        or value is None
                        or not comparator(value, limit)
                    ):
                        gate_failures.append(
                            f"{name}={value!r} 未达到 {direction} 门槛 {limit!r}"
                        )
        except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
            parser.exit(2, f"评估门槛无效：{exc}\n")
    for path in (args.json_output, args.report):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(render_markdown(report), encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "dataset": report.dataset_id,
                "metrics": report.metrics.model_dump(),
                "real_model_attempted": False,
                "database_accessed": False,
                "json_report": str(args.json_output),
                "markdown_report": str(args.report),
                "gate_passed": not gate_failures if args.gate else None,
                "gate_failures": gate_failures,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    if args.strict and report.metrics.passed_count != report.metrics.case_count:
        sys.stderr.write("严格验收未通过；失败样例和正确证据已保留在报告中。\n")
        return 1
    if gate_failures:
        sys.stderr.write("检索指标未达到固定门槛：" + "；".join(gate_failures) + "。\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
