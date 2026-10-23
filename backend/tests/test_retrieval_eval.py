import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app import knowledge
from app.retrieval_eval import (
    CorpusChunk,
    EvalCase,
    Evidence,
    aggregate,
    code_block,
    evaluate_case,
    load_dataset,
    table_text,
)

ROOT = Path(__file__).resolve().parents[2]


def gold(quote: str = "correct evidence", line: int = 2) -> Evidence:
    return Evidence(filename="notes.md", line_start=line, line_end=line, quote=quote)


def make_case(
    expected: list[Evidence] | None = None, scope: str | None = None
) -> EvalCase:
    return EvalCase(
        id="unit",
        category="direct",
        query="timeout",
        scope=scope,
        expected=[gold()] if expected is None else expected,
    )


def chunk(
    content: str,
    position: int = 0,
    filename: str = "notes.md",
    start: int = 1,
    end: int = 3,
) -> CorpusChunk:
    return CorpusChunk(filename, position, "Same heading", start, end, content)


def rank(monkeypatch: pytest.MonkeyPatch, values: list[tuple[int, float]]) -> None:
    monkeypatch.setattr(knowledge, "rank_chunks", lambda *args, **kwargs: values)


@pytest.mark.parametrize(
    "wrong",
    [
        chunk("timeout wrong paragraph"),
        chunk("timeout correct evidence", start=4, end=6),
        chunk("timeout correct evidence", filename="other.md"),
    ],
)
def test_matching_needs_file_lines_and_evidence_not_just_filename(
    monkeypatch: pytest.MonkeyPatch, wrong: CorpusChunk
) -> None:
    rank(monkeypatch, [(0, 2.0), (1, 1.0)])
    result = evaluate_case(make_case(), [wrong, chunk("timeout correct evidence", 1)])
    assert result.top1_hit is False and result.passed is False
    assert result.recall_at_5 == 1 and result.reciprocal_rank_at_5 == 0.5
    assert result.sources[0].evidence_ids == [] and result.sources[1].evidence_ids == [
        1
    ]
    assert result.failures == ["wrong_top1"]


def test_overlap_does_not_double_count_and_macro_recall_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank(monkeypatch, [(0, 3.0), (1, 2.0)])
    partial = evaluate_case(
        make_case([gold("evidence A"), gold("evidence B")]),
        [
            chunk("timeout evidence A"),
            chunk("timeout evidence A", 1),
            chunk("timeout evidence B", 2),
        ],
    )
    assert partial.recall_at_5 == 0.5 and not partial.passed
    rank(monkeypatch, [(0, 3.0), (1, 2.0)])
    second = evaluate_case(make_case(), [chunk("wrong"), chunk("correct evidence", 1)])
    combined = aggregate([partial, second])
    assert combined.top1_hit_rate == 0.5
    assert combined.recall_at_5 == 0.75 and combined.mrr_at_5 == 0.75


def test_multiple_evidence_items_can_be_supported_by_one_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank(monkeypatch, [(0, 1.0)])
    result = evaluate_case(
        make_case([gold("evidence A"), gold("evidence B")]),
        [chunk("evidence A and evidence B")],
    )
    assert result.passed and result.recall_at_5 == 1
    assert result.sources[0].evidence_ids == [1, 2]


def test_missing_evidence_from_chunking_is_not_silently_relabelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank(monkeypatch, [(0, 1.0)])
    result = evaluate_case(make_case(), [chunk("timeout evidence was truncated")])
    assert not result.passed and result.recall_at_5 == 0
    assert "evidence_not_in_chunks" in result.failures


def test_no_answer_metrics_and_empty_denominators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rank(monkeypatch, [])
    no_hit = evaluate_case(make_case([]), [chunk("timeout")])
    rank(monkeypatch, [(0, 1.0)])
    false_hit = evaluate_case(make_case([]), [chunk("timeout")])
    metrics = aggregate([no_hit, false_hit])
    assert metrics.no_hit_accuracy == 0.5 and metrics.false_positive_rate == 0.5
    assert (
        metrics.top1_hit_rate is None
        and metrics.recall_at_5 is None
        and metrics.mrr_at_5 is None
    )
    assert false_hit.failures == ["false_positive"]
    empty = aggregate([])
    assert empty.case_count == 0 and empty.no_hit_accuracy is None


def test_scope_filters_candidates_before_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def fake_rank(
        query: str, contents: list[str], limit: int
    ) -> list[tuple[int, float]]:
        assert query == "timeout" and limit == 5
        observed.extend(contents)
        return [(0, 1.0)]

    monkeypatch.setattr(knowledge, "rank_chunks", fake_rank)
    result = evaluate_case(
        make_case(scope="notes.md"),
        [
            chunk("timeout foreign secret", filename="foreign.md"),
            chunk("correct evidence"),
        ],
    )
    assert result.passed and len(result.sources) == 1
    assert "foreign secret" not in "\n".join(observed)
    assert result.sources[0].filename == "notes.md"


def dataset_files(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "notes.md").write_text(
        "# Heading\ncorrect evidence\n", encoding="utf-8"
    )
    (examples / "other.md").write_text("# Other\nother evidence\n", encoding="utf-8")
    data = {
        "schema_version": 2,
        "dataset_id": "unit",
        "description": "unit",
        "corpus": ["notes.md", "other.md"],
        "cases": [make_case().model_dump()],
    }
    path = tmp_path / "cases.json"
    return examples, path, data


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate_case",
        "duplicate_evidence",
        "wrong_quote",
        "wrong_lines",
        "unknown_scope",
        "outside_scope",
        "escape_path",
        "duplicate_filename",
        "empty_query",
        "empty_cases",
    ],
)
def test_bad_annotations_and_unsafe_paths_fail_validation(
    tmp_path: Path, invalid: str
) -> None:
    examples, path, data = dataset_files(tmp_path)
    case = data["cases"][0]
    if invalid == "duplicate_case":
        data["cases"].append(case)
    elif invalid == "duplicate_evidence":
        case["expected"].append(case["expected"][0])
    elif invalid == "wrong_quote":
        case["expected"][0]["quote"] = "not in source"
    elif invalid == "wrong_lines":
        case["expected"][0]["line_end"] = 99
    elif invalid == "unknown_scope":
        case["scope"] = "missing.md"
    elif invalid == "outside_scope":
        case["scope"] = "other.md"
    elif invalid == "escape_path":
        data["corpus"] = ["../private.txt"]
    elif invalid == "duplicate_filename":
        data["corpus"].append("notes.md")
    elif invalid == "empty_query":
        case["query"] = "   "
    elif invalid == "empty_cases":
        data["cases"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(examples, path)


def test_report_keeps_markdown_fences_and_html_literal() -> None:
    assert code_block("```\n<script>") == "````text\n```\n<script>\n````"
    assert table_text("a|b\n<script>") == "a\\|b &lt;script&gt;"


def run_offline(
    tmp_path: Path, seed: int, extra: list[str] | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    report = tmp_path / f"report-{seed}.json"
    script = ROOT / "backend/scripts/evaluate-retrieval.py"
    args = [
        str(script),
        "--json-output",
        str(report),
        "--report",
        str(tmp_path / f"report-{seed}.md"),
        *(extra or []),
    ]
    code = (
        "import runpy, sys\nfrom unittest.mock import patch\n"
        f"sys.argv = {args!r}\n"
        'with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):\n'
        '    result = runpy.run_path(sys.argv[0])["main"]()\n'
        'assert "app.core.db" not in sys.modules\n'
        'assert "app.core.config" not in sys.modules\n'
        "raise SystemExit(result)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "backend"),
            "PYTHONHASHSEED": str(seed),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, report


def test_offline_cli_needs_no_env_network_or_database_and_is_reproducible(
    tmp_path: Path,
) -> None:
    reports = []
    markdowns = []
    for seed in (1, 2, 3):
        result, output = run_offline(tmp_path, seed)
        assert result.returncode == 0, result.stderr
        reports.append(json.loads(output.read_text()))
        markdown = (tmp_path / f"report-{seed}.md").read_text(encoding="utf-8")
        assert "标注证据行号" in markdown and "首条（行号）" in markdown
        for item in reports[-1]["cases"]:
            assert f"| {item['case']['id']} |" in markdown
        markdowns.append(markdown)
    assert reports[0] == reports[1] == reports[2]
    assert markdowns[0] == markdowns[1] == markdowns[2]
    assert reports[0]["metrics"]["case_count"] == 30
    assert (
        reports[0]["database_accessed"] is False
        and reports[0]["real_model_attempted"] is False
    )
    strict, output = run_offline(tmp_path, 4, ["--strict"])
    assert strict.returncode == 1 and output.is_file()
    assert "严格验收未通过" in strict.stderr
    gate, output = run_offline(
        tmp_path,
        6,
        ["--gate", str(ROOT / "examples/retrieval-eval-gate.json")],
    )
    assert gate.returncode == 0, gate.stderr
    assert json.loads(gate.stdout.splitlines()[-1])["gate_passed"] is True
    failing_gate = tmp_path / "failing-gate.json"
    failing_gate.write_text(
        json.dumps(
            {
                "dataset_id": "devassist-authored-notes-v2",
                "minimums": {"passed_count": 31},
                "maximums": {},
            }
        ),
        encoding="utf-8",
    )
    failed, _ = run_offline(tmp_path, 7, ["--gate", str(failing_gate)])
    assert failed.returncode == 1 and "passed_count" in failed.stderr
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    rejected, output = run_offline(tmp_path, 5, ["--dataset", str(invalid)])
    assert rejected.returncode == 2 and not output.exists()
