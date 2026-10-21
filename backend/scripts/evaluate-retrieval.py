"""Generate offline evidence-level reports; --strict gates every annotated case."""

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
    args = parser.parse_args()
    try:
        report = evaluate(root / "examples", args.dataset)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"评估数据无效：{exc}\n")
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
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    if args.strict and report.metrics.passed_count != report.metrics.case_count:
        sys.stderr.write("严格验收未通过；失败样例和正确证据已保留在报告中。\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
