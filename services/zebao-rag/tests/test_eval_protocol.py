"""Check offline/online text encoding arguments without loading a real model."""

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path


class EvaluationProtocolTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("sqlmodel"), "requires the eval dependency group")
    def test_worker_query_and_document_protocols(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
import runpy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from zebao_rag import embedder
from zebao_rag.worker import TEXT_INSTRUCTION

module = runpy.run_path(str(Path.cwd().parent / "services/zebao-rag/scripts/finetune/eval_retrieval.py"))

class FakeModel:
    def __init__(self):
        self.calls = []
    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

model = FakeModel()
assert module["encoding_protocol_metadata"](model)["document_prompt_status"] == "unknown"
model.default_prompt_name = "default"
model.prompts = {"default": "local model default"}
metadata = module["encoding_protocol_metadata"](model)
assert metadata["document_prompt_override"] is None
assert metadata["document_prompt_status"] == "model_default_applied"
assert metadata["model_default_prompt_name"] == "default"
assert metadata["model_default_prompt_sha256"] == __import__("hashlib").sha256(b"local model default").hexdigest()
with patch.object(embedder, "get_model", return_value=model):
    online_doc = embedder.encode_texts(["document"])
    online_query = embedder.encode_texts(["question"], is_query=True, instruction=TEXT_INSTRUCTION)
offline_doc = module["encode"](model, ["document"], 8)
offline_query = module["encode"](model, ["question"], 8, is_query=True)
assert np.array_equal(online_doc, offline_doc)
assert np.array_equal(online_query, offline_query)
for _, kwargs in model.calls:
    assert kwargs["normalize_embeddings"] is True
    assert kwargs["convert_to_numpy"] is True
assert "prompt" not in model.calls[0][1] and "prompt" not in model.calls[2][1]
assert model.calls[1][1]["prompt"] == model.calls[3][1]["prompt"] == TEXT_INSTRUCTION

model.calls.clear()
chunk = module["Chunk"]("a.txt", 0, "heading", 1, 1, "evidence")
case = {"id": "1", "query": "question", "scope": None, "expected": []}
module["score_cases"]([case], [chunk], np.asarray([[1.0, 0.0]]), model, 8)
assert model.calls[-1][1]["prompt"] == TEXT_INSTRUCTION
model.calls.clear()
module["val_metrics"](model, [{"id": "a", "text": "document"}], [{"chunk_id": "a", "query": "question"}], 8)
assert "prompt" not in model.calls[0][1]
assert model.calls[1][1]["prompt"] == TEXT_INSTRUCTION
'''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1")
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=root.parents[1] / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
