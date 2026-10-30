"""Offline snapshot and holdout tests; no model, network or database required."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts" / "finetune"
sys.path.insert(0, str(SCRIPT_DIR))
from dataset_snapshot import read_snapshot, text_sha256  # noqa: E402


def write_snapshot(work, corpus, queries, *, legacy=False):
    corpus_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode() for row in corpus
    )
    (work / "corpus.jsonl").write_bytes(corpus_bytes)
    (work / "queries.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queries),
        encoding="utf-8",
    )
    stats = {"val_sources": ["val.md"]}
    if not legacy:
        stats["corpus_sha256"] = hashlib.sha256(corpus_bytes).hexdigest()
    (work / "corpus_stats.json").write_text(json.dumps(stats), encoding="utf-8")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.corpus = [
            {"id": "train-0", "source": "train.md", "text": "train distractor"},
            {"id": "val-0", "source": "val.md", "text": "heldout answer"},
        ]
        self.query = {
            "chunk_id": "val-0", "source": "val.md", "query": "question",
            "chunk_sha256": text_sha256("heldout answer"),
        }

    def test_content_change_and_corpus_tampering_are_rejected(self):
        write_snapshot(self.work, self.corpus, [self.query])
        _, _, _, meta = read_snapshot(self.work)
        self.assertEqual(meta["question_provenance"], "content_verified")
        changed = [self.corpus[0], {**self.corpus[1], "text": "changed"}]
        write_snapshot(self.work, changed, [self.query])
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            read_snapshot(self.work)
        (self.work / "corpus.jsonl").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed since"):
            read_snapshot(self.work)

    def test_legacy_requires_explicit_opt_in_and_stays_marked(self):
        old_query = {k: v for k, v in self.query.items() if k != "chunk_sha256"}
        write_snapshot(self.work, self.corpus, [old_query], legacy=True)
        with self.assertRaisesRegex(ValueError, "legacy snapshot"):
            read_snapshot(self.work)
        self.assertEqual(
            read_snapshot(self.work, allow_legacy=True)[3]["question_provenance"],
            "legacy_unverified",
        )

    def test_duplicate_and_missing_chunks_fail_closed(self):
        write_snapshot(self.work, self.corpus, [self.query, self.query])
        with self.assertRaisesRegex(ValueError, "duplicate question"):
            read_snapshot(self.work)
        write_snapshot(self.work, self.corpus + [self.corpus[0]], [self.query])
        with self.assertRaisesRegex(ValueError, "duplicate chunk"):
            read_snapshot(self.work)
        write_snapshot(self.work, self.corpus[:1], [self.query])
        with self.assertRaisesRegex(ValueError, "missing chunk"):
            read_snapshot(self.work)

    def test_heldout_questions_do_not_change_training_pairs_hash(self):
        training = {"chunk_id": "train-0", "source": "train.md", "query": "train question",
                    "chunk_sha256": text_sha256("train distractor")}
        write_snapshot(self.work, self.corpus, [training])
        first = read_snapshot(self.work)[3]
        write_snapshot(self.work, self.corpus, [training, self.query])
        second = read_snapshot(self.work)[3]
        self.assertEqual(first["training_pairs_sha256"], second["training_pairs_sha256"])
        self.assertNotEqual(first["queries_sha256"], second["queries_sha256"])
        training["query"] = "different question"
        write_snapshot(self.work, self.corpus, [training, self.query])
        self.assertNotEqual(first["training_pairs_sha256"], read_snapshot(self.work)[3]["training_pairs_sha256"])

    def test_corpus_builder_does_not_overwrite_existing_experiment(self):
        existing = self.work / "corpus.jsonl"
        existing.write_text("keep", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "corpus.py"), "--out-dir", str(self.work)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(existing.read_text(), "keep")

    def test_query_generation_rejects_stale_data_before_network(self):
        changed = [self.corpus[0], {**self.corpus[1], "text": "changed"}]
        write_snapshot(self.work, changed, [self.query])
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "generate_queries.py"), "--work-dir", str(self.work)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no longer matches chunk content", result.stderr)
        self.assertEqual(json.loads((self.work / "queries.jsonl").read_text()), self.query)

    @unittest.skipUnless(importlib.util.find_spec("sqlmodel"), "requires eval dependency group")
    def test_heldout_questions_compete_against_all_corpus(self):
        write_snapshot(self.work, self.corpus, [self.query])
        script = r'''
import runpy, sys
from pathlib import Path
import numpy as np
module = runpy.run_path(sys.argv[1])
examples = Path(sys.argv[2]) / "examples"
examples.mkdir()
dataset = examples / "dataset.json"
dataset.write_text(__import__("json").dumps({"corpus": ["../outside.txt"]}))
(examples.parent / "outside.txt").write_text("outside corpus root")
try:
    module["load_eval_set"](examples, dataset)
    raise AssertionError("corpus path escaped examples root")
except ValueError:
    pass
inside = examples / "inside.txt"
inside.write_text("original synthetic document")
dataset.write_text(__import__("json").dumps({"corpus": ["inside.txt"]}))
_, _, frozen = module["load_eval_set"](examples, dataset, include_metadata=True)
assert frozen["corpus_sha256"]["inside.txt"] == __import__("hashlib").sha256(inside.read_bytes()).hexdigest()
module["verify_examples_unchanged"](examples, dataset, frozen)
inside.write_text("changed synthetic document")
try:
    module["verify_examples_unchanged"](examples, dataset, frozen)
    raise AssertionError("changed example corpus was accepted")
except ValueError:
    pass
inside.write_text("original synthetic document")
dataset.write_text(__import__("json").dumps({"corpus": []}))
try:
    module["verify_examples_unchanged"](examples, dataset, frozen)
    raise AssertionError("changed example dataset was accepted")
except ValueError:
    pass
corpus, questions, metadata = module["val_dataset"](Path(sys.argv[2]))
assert len(corpus) == 2 and len(questions) == 1
assert metadata["candidate_scope"] == "all_corpus"
assert metadata["candidate_count"] == 2 and metadata["heldout_query_count"] == 1
class Model:
    def encode(self, texts, **kwargs):
        values = {"train distractor": [1., 0.], "heldout answer": [.8, .6], "question": [1., 0.]}
        return np.asarray([values[x] for x in texts])
full = module["val_metrics"](Model(), corpus, questions, 8)
heldout_only = module["val_metrics"](Model(), corpus[1:], questions, 8)
assert full["top1"] == 0.0 and heldout_only["top1"] == 1.0
adapter = Path(sys.argv[2]) / "adapter"
adapter.mkdir()
try:
    module["adapter_provenance"](adapter, metadata, allow_unverified=False)
    raise AssertionError("missing training metadata was accepted")
except ValueError:
    pass
assert module["adapter_provenance"](adapter, metadata, allow_unverified=True) == "legacy_unverified"
training = runpy.run_path(str(Path(sys.argv[1]).parent / "train_lora.py"))
settings = __import__("types").SimpleNamespace(epochs=3, batch_size=8, model="local",
    seed=123, max_length=256, max_steps=10)
plan = training["training_plan"](settings, 16, metadata)
assert plan["seed"] == 123 and plan["max_length"] == 256 and plan["max_steps"] == 10
assert plan["model_artifact_identity_verified"] is False and plan["model_revision"] == "unknown"
assert "torch" in plan["dependency_versions"] and plan["dataset_snapshot"] == metadata
old_meta = Path(sys.argv[2]) / "train_meta.json"
old_meta.write_text("historical experiment")
record = {"dataset_snapshot": {"training_pairs_sha256": metadata["training_pairs_sha256"],
                               "question_provenance": "content_verified"}}
assert training["write_training_meta"](adapter, record) == adapter / "train_meta.json"
assert old_meta.read_text() == "historical experiment"
assert module["adapter_provenance"](adapter, metadata, allow_unverified=False) == "training_data_matches_metadata"
try:
    training["write_training_meta"](adapter, {"overwritten": True})
    raise AssertionError("existing adapter metadata was overwritten")
except FileExistsError:
    pass
assert module["adapter_provenance"](adapter, metadata, allow_unverified=False) == "training_data_matches_metadata"
metadata["training_pairs_sha256"] = "changed"
try:
    module["adapter_provenance"](adapter, metadata, allow_unverified=True)
    raise AssertionError("mismatched training data was accepted")
except ValueError:
    pass
import shutil
empty = Path(sys.argv[2]) / "empty"
empty.mkdir()
for name in ("corpus.jsonl", "corpus_stats.json"):
    shutil.copyfile(Path(sys.argv[2]) / name, empty / name)
(empty / "queries.jsonl").write_text("")
try:
    module["val_dataset"](empty)
    raise AssertionError("empty holdout was silently accepted")
except ValueError:
    pass
_, empty_queries, empty_meta = module["val_dataset"](empty, allow_empty_heldout=True)
assert empty_queries == [] and empty_meta["heldout_evaluable"] is False
from unittest.mock import patch
model_dir = Path(sys.argv[2]) / "fake-model"
model_dir.mkdir()
(model_dir / "config.json").write_text("immutable synthetic artifact")
assert module["resolve_model_snapshot"](str(model_dir), allow_download=False) == model_dir.resolve()
with patch("huggingface_hub.snapshot_download", side_effect=FileNotFoundError("no cached snapshot")) as fetch:
    try:
        module["resolve_model_snapshot"]("not/cached", allow_download=False)
        raise AssertionError("missing local model was accepted")
    except FileNotFoundError:
        pass
    assert fetch.call_args.kwargs["local_files_only"] is True
with patch("huggingface_hub.snapshot_download", return_value=str(model_dir)) as fetch:
    module["resolve_model_snapshot"]("test/model", allow_download=True)
    assert fetch.call_args.kwargs["local_files_only"] is False
outside = Path(sys.argv[2]) / "secret.txt"
outside.write_text("unrelated")
(model_dir / "escape").symlink_to(outside)
try:
    module["checked_artifact_fingerprints"](model_dir, None)
    raise AssertionError("model symlink escaped approved roots")
except ValueError:
    pass
(model_dir / "escape").unlink()
safe_work = Path(sys.argv[2]) / "safe"
safe_work.mkdir()
for name in ("corpus.jsonl", "corpus_stats.json", "queries.jsonl"):
    shutil.copyfile(Path(sys.argv[2]) / name, safe_work / name)
import contextlib, io, types
class Baseline:
    default_prompt_name = None
    prompts = {}
    def __init__(self, path, **kwargs):
        assert path == str(model_dir.resolve()) and kwargs["local_files_only"] is True
    def encode(self, texts, **kwargs):
        return np.asarray([[1., 0.] for _ in texts], dtype=np.float32)
report = safe_work / "report.json"
argv = ["eval", "--work-dir", str(safe_work), "--model", str(model_dir),
        "--output", str(report), "--device", "cpu"]
with patch.dict(sys.modules, {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=Baseline)}), \
     patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
    assert module["main"]() == 0
result = __import__("json").loads(report.read_text())
assert result["model_artifact_content_checked"] is True
assert result["cross_run_model_identity_verified"] is False
assert result["model_artifact_sha256"] == module["checked_artifact_fingerprints"](model_dir, None)[0]
assert result["model_revision"] == "unknown" and result["model_download_allowed"] is False
assert result["model_load_mode"] == "resolved_local_directory_only"
class Changing(Baseline):
    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        (model_dir / "config.json").write_text("changed while loading")
second_report = safe_work / "changed.json"
argv[-3] = str(second_report)
with patch.dict(sys.modules, {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=Changing)}), \
     patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
    try:
        module["main"]()
        raise AssertionError("changed model artifact was accepted")
    except RuntimeError as exc:
        assert "changed during base model loading" in str(exc)
assert not second_report.exists()
(model_dir / "config.json").write_text("immutable synthetic artifact")
code_path = safe_work / "synthetic-code.py"
code_path.write_text("original code")
class ChangingSource(Baseline):
    def encode(self, texts, **kwargs):
        code_path.write_text("changed code")
        return super().encode(texts, **kwargs)
source_report = safe_work / "source_changed.json"
argv[-3] = str(source_report)
code_paths = lambda: {"evaluator": code_path, "knowledge": code_path}
with patch.dict(sys.modules, {"sentence_transformers": types.SimpleNamespace(SentenceTransformer=ChangingSource)}), \
     patch.dict(module["main"].__globals__, {"code_snapshot_paths": code_paths}), \
     patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
    try:
        module["main"]()
        raise AssertionError("changed evaluator source was accepted")
    except RuntimeError as exc:
        assert "source files changed during run" in str(exc)
assert not source_report.exists()
'''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1")
        result = subprocess.run(
            [sys.executable, "-c", script, str(SCRIPT_DIR / "eval_retrieval.py"), str(self.work)],
            cwd=ROOT.parents[1] / "backend", env=env,
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
