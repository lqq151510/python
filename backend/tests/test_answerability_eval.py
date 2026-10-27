import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from app.answerability_eval import (
    BASELINE_DOCUMENT_SYSTEM_PROMPT,
    BASELINE_PROMPT_SHA256,
    Dataset,
    classify_response,
    load_dataset,
    messages_for,
    sources_for,
    summarize_results,
    validate_base_url,
)
from app.grounded_answer import INSUFFICIENT_EVIDENCE_ANSWER

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "backend/scripts/evaluate-answerability.py"


def dataset_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_id": "unit-answerability",
        "description": "合成资料，不是实际服务配置。",
        "documents": [
            {"id": "cache", "filename": "cache.txt", "text": "缓存保留十五分钟。"}
        ],
        "cases": [
            {
                "id": "positive",
                "document_id": "cache",
                "question": "缓存保留多久？",
                "answerable": True,
                "evidence_quotes": ["缓存保留十五分钟。"],
            },
            {
                "id": "negative",
                "document_id": "cache",
                "question": "缓存容量上限是多少？",
                "answerable": False,
                "evidence_quotes": [],
            },
        ],
    }


def completion(content: Any, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}]
    }


def grounded_content(answerable: bool = True) -> str:
    return json.dumps(
        {
            "answerable": answerable,
            "answer": "缓存保留十五分钟。[S1]" if answerable else "伪造的容量是999。",
            "evidence": [{"citation": 1, "quote": "缓存保留十五分钟。"}]
            if answerable
            else [],
        },
        ensure_ascii=False,
    )


@pytest.fixture
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("answerability_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_dataset_and_baseline_prompt_are_unchanged() -> None:
    data, digest = load_dataset(ROOT / "examples/answerability-eval.json")
    assert digest == "0bb476887de9aecb38fba287f5895028a11c3894f5958daaeeef40991c0e3d65"
    assert len(data.documents) == 3 and len(data.cases) == 12
    assert sum(case.answerable for case in data.cases) == 6
    assert (
        hashlib.sha256(BASELINE_DOCUMENT_SYSTEM_PROMPT.encode()).hexdigest()
        == BASELINE_PROMPT_SHA256
    )


@pytest.mark.parametrize(
    "change",
    [
        "unknown_document",
        "duplicate_document",
        "duplicate_case",
        "wrong_quote",
        "negative_quote",
        "positive_no_quote",
        "wrong_bool",
        "extra_field",
    ],
)
def test_dataset_rejects_invalid_annotations(change: str) -> None:
    payload = dataset_payload()
    if change == "unknown_document":
        payload["cases"][0]["document_id"] = "missing"
    elif change == "duplicate_document":
        payload["documents"].append(copy.deepcopy(payload["documents"][0]))
    elif change == "duplicate_case":
        payload["cases"].append(copy.deepcopy(payload["cases"][0]))
    elif change == "wrong_quote":
        payload["cases"][0]["evidence_quotes"] = ["文档没有这句话"]
    elif change == "negative_quote":
        payload["cases"][1]["evidence_quotes"] = ["缓存保留十五分钟。"]
    elif change == "positive_no_quote":
        payload["cases"][0]["evidence_quotes"] = []
    elif change == "wrong_bool":
        payload["cases"][0]["answerable"] = "true"
    else:
        payload["cases"][0]["unexpected"] = "value"
    with pytest.raises(ValueError):
        Dataset.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1/",
        "http://[::1]:11434/v1",
    ],
)
def test_only_explicit_loopback_targets_are_accepted(value: str) -> None:
    assert validate_base_url(value) == value.rstrip("/")


@pytest.mark.parametrize(
    "value",
    [
        "https://api.example.com/v1",
        "http://127.0.0.1.example.com/v1",
        "http://127.0.0.1@api.example.com/v1",
        "http://user:secret@localhost/v1",
        "http://127.0.0.1/v1?api_key=secret",
        "http://127.0.0.1/v1#fragment",
        "http://127.0.0.1/v1/other",
        "ftp://127.0.0.1/v1",
        "http://127.0.0.1:99999/v1",
        "http://[invalid/v1",
    ],
)
def test_remote_credentials_redirectable_paths_and_bad_ports_are_rejected(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="base-url"):
        validate_base_url(value)


def test_baseline_prompt_and_context_match_the_frozen_request_shape() -> None:
    data = Dataset.model_validate(dataset_payload())
    sources = sources_for(data.documents[0])
    messages = messages_for("baseline", "问题？", sources)
    assert messages[0] == {"role": "system", "content": BASELINE_DOCUMENT_SYSTEM_PROMPT}
    assert messages[1]["content"] == (
        "问题：\n问题？\n\n参考文档（只读数据）：\n"
        "[S1] 文件=cache.txt; 标题=完整原文；未执行检索; 行=1-1\n缓存保留十五分钟。"
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"choices": []},
        completion(None),
        completion(" "),
        completion(grounded_content(), "length"),
        completion("not JSON"),
        completion(
            '{"answerable":true,"answer":"999。[S2]","evidence":[{"citation":2,"quote":"伪造"}]}'
        ),
    ],
)
def test_invalid_outputs_are_never_counted_as_refusals(payload: Any) -> None:
    data = Dataset.model_validate(dataset_payload())
    result = classify_response("grounded", payload, sources_for(data.documents[0]))
    assert result["outcome"] == "invalid_response"
    assert result["error"]


def test_answer_refusal_and_baseline_markers_are_distinct() -> None:
    data = Dataset.model_validate(dataset_payload())
    sources = sources_for(data.documents[0])
    answered = classify_response("grounded", completion(grounded_content()), sources)
    refused = classify_response(
        "grounded", completion(grounded_content(False)), sources
    )
    baseline = classify_response(
        "baseline", completion("资料未提供容量，无法确定。"), sources
    )
    assert (
        answered["outcome"] == "answered" and answered["quote_provenance_valid"] is True
    )
    assert refused["outcome"] == "insufficient_evidence"
    assert refused["answer"] == INSUFFICIENT_EVIDENCE_ANSWER
    assert "999" not in refused["answer"]
    assert (
        baseline["outcome"] == "answered"
        and baseline["heuristic_refusal_marker"] is True
    )
    rows = []
    for result, expected in [(answered, True), (refused, False)]:
        rows.append(
            {**result, "expected_answerable": expected, "provider_call_count": 1}
        )
    for outcome in ["invalid_response", "request_error"]:
        rows.append(
            {
                **refused,
                "outcome": outcome,
                "expected_answerable": False,
                "provider_call_count": 1,
            }
        )
    summary = summarize_results("grounded", rows)
    assert summary["positive_answered_count"] == 1
    assert summary["negative_explicit_refusal_count"] == 1
    assert summary["outcomes"]["invalid_response"] == 1
    assert summary["outcomes"]["request_error"] == 1
    assert summary["semantic_accuracy"] is None
    assert (
        summarize_results("baseline", rows)["negative_explicit_refusal_count"] is None
    )


def run_args(tmp_path: Path, mode: str = "both") -> argparse.Namespace:
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps(dataset_payload(), ensure_ascii=False))
    return argparse.Namespace(
        dataset=dataset,
        base_url="http://127.0.0.1:11434/v1",
        mode=mode,
        model="offline-mock",
        json_output=tmp_path / "results.json",
        report=tmp_path / "report.md",
    )


def test_paired_runner_saves_fingerprints_before_calls_and_uses_one_call_per_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = run_args(tmp_path)
    requests: list[dict[str, Any]] = []
    options: dict[str, Any] = {}
    real_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        saved = json.loads(args.json_output.read_text())
        assert saved["completed"] is False
        assert saved["dataset_sha256"] and saved["validator_code_sha256"]
        assert len(saved["cases"]) == len(requests)
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        assert (
            payload["max_tokens"] == 1024
            and payload["temperature"] == 0
            and payload["seed"] == 0
        )
        structured = "response_format" in payload
        if structured:
            assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200, json=completion(grounded_content() if structured else "需要人工复核。")
        )

    def client(**kwargs: Any) -> httpx.AsyncClient:
        options.update(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(runner.httpx, "AsyncClient", client)
    assert asyncio.run(runner.run(args, ROOT)) == 0
    report = json.loads(args.json_output.read_text())
    assert len(requests) == report["provider_call_count"] == 4
    assert options["trust_env"] is False and options["follow_redirects"] is False
    assert options["timeout"].connect == 5 and options["timeout"].read == 30
    assert report["completed"] is True and report["real_model_attempted"] is True
    assert (
        report["provided_context_only"] is True and report["database_accessed"] is False
    )
    assert [row["mode"] for row in report["cases"]] == [
        "baseline",
        "grounded",
        "baseline",
        "grounded",
    ]
    assert report["summaries"]["grounded"]["negative_explicit_refusal_count"] == 0
    assert "缓存保留十五分钟" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure", ["invalid_json", "redirect", "unavailable", "read_timeout"]
)
def test_provider_failures_return_nonzero_without_retries_or_following_redirects(
    failure: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: ModuleType
) -> None:
    args = run_args(tmp_path, mode="grounded")
    requests = 0
    real_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.host == "127.0.0.1"
        if failure == "read_timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        if failure == "redirect":
            return httpx.Response(
                302,
                headers={"Location": "https://external.example/v1/chat/completions"},
            )
        if failure == "unavailable":
            return httpx.Response(503)
        return httpx.Response(200, text="not JSON")

    monkeypatch.setattr(
        runner.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    assert asyncio.run(runner.run(args, ROOT)) == 1
    report = json.loads(args.json_output.read_text())
    assert requests == report["provider_call_count"] == 2
    assert report["summaries"]["grounded"]["negative_explicit_refusal_count"] == 0
    assert all(
        row["outcome"] in {"request_error", "invalid_response"}
        for row in report["cases"]
    )


def test_cli_import_and_input_rejection_need_no_settings_database_or_network(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=DO_NOT_READ\nPOSTGRES_PORT=not_an_integer\n"
    )
    command = f"""
import runpy, socket, sys
def denied(*args, **kwargs):
    raise AssertionError('network is forbidden')
socket.getaddrinfo = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
sys.modules['app.core.config'] = None
sys.modules['app.core.db'] = None
sys.argv = [{str(RUNNER)!r}, '--dataset', {str(ROOT / "examples/answerability-eval.json")!r}, '--base-url', 'https://external.example/v1']
runpy.run_path({str(RUNNER)!r}, run_name='__main__')
"""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "backend")}
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "base-url" in result.stderr and not result.stdout
    assert "DO_NOT_READ" not in result.stderr
