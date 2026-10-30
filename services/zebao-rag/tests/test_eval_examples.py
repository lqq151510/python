"""Executable synthetic-corpus runner tests with no model, database or network."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("sqlmodel"), "requires the eval dependency group")
class ExamplesRunnerTests(unittest.TestCase):
    def test_offline_runner_freezes_sources_and_never_overwrites(self):
        program = r'''
import contextlib, io, json, runpy, socket, sys, types
from pathlib import Path
from unittest.mock import patch
import numpy as np
root = Path(sys.argv[1])
work = Path(sys.argv[2])
model = work / "model"
model.mkdir()
(model / "config.json").write_text("synthetic local model")
sys.path.insert(0, str(root / "scripts/finetune"))
import eval_retrieval as evaluation
runner = runpy.run_path(str(root / "scripts/finetune/eval_examples.py"))
class FakeModel:
    default_prompt_name = None
    prompts = {}
    def __init__(self, path, **kwargs):
        assert path == str(model.resolve()) and kwargs["local_files_only"] is True
        try:
            socket.create_connection(("127.0.0.1", 1))
            raise AssertionError("network was not blocked")
        except RuntimeError as exc:
            assert "network disabled" in str(exc)
    def encode(self, texts, **kwargs):
        return np.asarray([[1., 0.] for _ in texts], dtype=np.float32)
report = work / "report.json"
args = ["eval_examples", "--model", str(model), "--output", str(report), "--device", "cpu"]
with patch.dict(sys.modules, {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=FakeModel)}), \
     patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
    assert runner["main"]() == 0
result = json.loads(report.read_text())
assert result["case_total"] == 54
assert [item["case_count"] for item in result["datasets"]] == [30, 24]
assert result["adapter"] == "not loaded"
assert result["source_sha256"]["examples_runner"]
assert result["model_artifact_sha256"] == evaluation.checked_artifact_fingerprints(model, None)[0]
prior = report.read_bytes()
with patch.object(sys, "argv", args), contextlib.redirect_stderr(io.StringIO()):
    try:
        runner["main"]()
        raise AssertionError("existing report was overwritten")
    except SystemExit as exc:
        assert exc.code == 2
assert report.read_bytes() == prior
source = work / "synthetic-code.py"
source.write_text("before")
class ChangingSource(FakeModel):
    def encode(self, texts, **kwargs):
        source.write_text("after")
        return super().encode(texts, **kwargs)
second = work / "changed.json"
args[4] = str(second)
with patch.dict(sys.modules, {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=ChangingSource)}), \
     patch.object(evaluation, "code_snapshot_paths", return_value={"synthetic": source}), \
     patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
    try:
        runner["main"]()
        raise AssertionError("changed source was accepted")
    except RuntimeError as exc:
        assert "source files changed" in str(exc)
assert not second.exists()
'''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1")
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-c", program, str(ROOT), tmp],
                cwd=ROOT.parents[1] / "backend",
                env=env, capture_output=True, text=True, timeout=90,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
