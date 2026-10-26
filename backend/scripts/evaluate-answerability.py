"""Run a paired local-model evaluation with supplied context, never retrieval."""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from app.answerability_eval import (
    BASELINE_DOCUMENT_SYSTEM_PROMPT,
    BASELINE_PROMPT_SHA256,
    Mode,
    classify_response,
    load_dataset,
    messages_for,
    render_markdown,
    sources_for,
    summarize_results,
    validate_base_url,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_report(report: dict[str, Any], output: Path, markdown: Path) -> None:
    for mode in report["modes"]:
        report["summaries"][mode] = summarize_results(
            mode, [result for result in report["cases"] if result["mode"] == mode]
        )
    report["provider_call_count"] = sum(
        result["provider_call_count"] for result in report["cases"]
    )
    for path in (output, markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    markdown.write_text(render_markdown(report), encoding="utf-8")


async def run(args: argparse.Namespace, root: Path) -> int:
    dataset, dataset_hash = load_dataset(args.dataset)
    base_url = validate_base_url(args.base_url)
    modes: list[Mode] = ["baseline", "grounded"] if args.mode == "both" else [args.mode]
    candidate = root / "backend/app/grounded_answer.py"
    prompt_hashes = {"baseline": BASELINE_PROMPT_SHA256}
    if "grounded" in modes:
        from app.grounded_answer import DOCUMENT_SYSTEM_PROMPT

        prompt_hashes["grounded"] = hashlib.sha256(
            DOCUMENT_SYSTEM_PROMPT.encode()
        ).hexdigest()
    if (
        hashlib.sha256(BASELINE_DOCUMENT_SYSTEM_PROMPT.encode()).hexdigest()
        != BASELINE_PROMPT_SHA256
    ):
        raise ValueError("冻结的 baseline prompt 指纹不匹配。")
    parameters = {
        "base_url": base_url,
        "model": args.model,
        "max_tokens": 1024,
        "temperature": 0,
        "seed": 0,
        "request_total_timeout_seconds": 30,
        "connect_timeout_seconds": 5,
        "retries": 0,
        "trust_env": False,
        "follow_redirects": False,
        "json_response_format_modes": ["grounded"],
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": dataset_hash,
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "provided_context_only": True,
        "database_accessed": False,
        "simulation": False,
        "real_model_attempted": False,
        "modes": modes,
        "parameters": parameters,
        "prompt_sha256": prompt_hashes,
        "validator_code_sha256": sha256(candidate) if "grounded" in modes else None,
        "evaluation_code_sha256": sha256(root / "backend/app/answerability_eval.py"),
        "runner_code_sha256": sha256(Path(__file__)),
        "expected_case_count_per_mode": len(dataset.cases),
        "provider_call_count": 0,
        "cases": [],
        "summaries": {},
        "completed": False,
    }
    save_report(report, args.json_output, args.report)
    documents = {document.id: document for document in dataset.documents}
    async with httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30, connect=5),
    ) as client:
        for case in dataset.cases:
            sources = sources_for(documents[case.document_id])
            for mode in modes:
                payload: dict[str, Any] = {
                    "model": args.model,
                    "messages": messages_for(mode, case.question, sources),
                    "max_tokens": 1024,
                    "temperature": 0,
                    "seed": 0,
                    "stream": False,
                }
                if mode == "grounded":
                    payload["response_format"] = {"type": "json_object"}
                result: dict[str, Any] = {
                    "case_id": case.id,
                    "document_id": case.document_id,
                    "question": case.question,
                    "expected_answerable": case.answerable,
                    "expected_evidence_quotes": case.evidence_quotes,
                    "mode": mode,
                    "provider_call_count": 1,
                    "outcome": "request_error",
                    "answer": None,
                    "raw_model_content": None,
                    "raw_model_response": None,
                    "finish_reason": None,
                    "heuristic_refusal_marker": None,
                    "quote_provenance_valid": None,
                    "error": None,
                }
                start = monotonic()
                report["real_model_attempted"] = True
                try:
                    async with asyncio.timeout(30):
                        response = await client.post(
                            f"{base_url}/chat/completions", json=payload
                        )
                    result["raw_model_response"] = response.text
                    response.raise_for_status()
                    try:
                        provider_payload = response.json()
                    except ValueError:
                        result["outcome"] = "invalid_response"
                        result["error"] = "provider response is not JSON"
                    else:
                        result.update(
                            classify_response(mode, provider_payload, sources)
                        )
                except httpx.HTTPStatusError as exc:
                    result["error"] = f"provider HTTP status {exc.response.status_code}"
                except (httpx.RequestError, TimeoutError) as exc:
                    result["error"] = type(exc).__name__
                result["latency_seconds"] = round(monotonic() - start, 3)
                report["cases"].append(result)
                save_report(report, args.json_output, args.report)
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["completed"] = True
    save_report(report, args.json_output, args.report)
    sys.stdout.write(
        json.dumps(
            {
                "real_model_attempted": report["real_model_attempted"],
                "provided_context_only": True,
                "database_accessed": False,
                "modes": report["summaries"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return int(
        any(
            result["outcome"] in {"request_error", "invalid_response"}
            for result in report["cases"]
        )
    )


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="直接提供指定原文的本地回答/拒答评估；不读取 .env、数据库或云端 API。"
    )
    parser.add_argument(
        "--mode", choices=("baseline", "grounded", "both"), default="both"
    )
    parser.add_argument(
        "--dataset", type=Path, default=root / "examples/answerability-eval.json"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5-1.5b-instruct:latest")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=root / ".local/answerability-eval/results.json",
    )
    parser.add_argument(
        "--report", type=Path, default=root / ".local/answerability-eval/report.md"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args, root))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"评估输入或报告路径无效：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
