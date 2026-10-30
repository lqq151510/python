"""Identity checks with synthetic files only; no model, credentials or database."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from zebao_rag.identity import (
    IdentityUnavailable,
    artifact_fingerprints,
    implementation_fingerprint,
    make_identity,
    valid_identity,
)


class IdentityTests(unittest.TestCase):
    def test_content_and_instruction_change_expected_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model, adapter = root / "model", root / "adapter"
            model.mkdir()
            adapter.mkdir()
            (model / "weights").write_bytes(b"base-v1")
            (adapter / "weights").write_bytes(b"adapter-v1")
            first = artifact_fingerprints(model, adapter)
            with patch("zebao_rag.identity.importlib.metadata.version", return_value="1"):
                original = make_identity(first, "instruction-a", "f" * 64)
                restarted = make_identity(first, "instruction-a", "f" * 64)
                different_prompt = make_identity(first, "instruction-b", "f" * 64)
                (adapter / "weights").write_bytes(b"adapter-v2")
                modified = make_identity(artifact_fingerprints(model, adapter), "instruction-a", "f" * 64)
            self.assertTrue(valid_identity(original))
            self.assertEqual(original["space_id"], restarted["space_id"])
            self.assertEqual(original["protocol_id"], restarted["protocol_id"])
            self.assertNotEqual(original["generation"], restarted["generation"])
            self.assertEqual(original["space_id"], different_prompt["space_id"])
            self.assertNotEqual(original["protocol_id"], different_prompt["protocol_id"])
            self.assertNotEqual(original["space_id"], modified["space_id"])

    def test_loaded_implementation_and_dependency_changes_change_space_identity(self):
        def encode_v1(value):
            return value + 1

        def encode_v2(value):
            return value + 2

        first = implementation_fingerprint({"encode": encode_v1})
        second = implementation_fingerprint({"encode": encode_v2})
        self.assertNotEqual(first, second)
        with patch("zebao_rag.identity.importlib.metadata.version", return_value="1"):
            base = make_identity(("a" * 64, None), "query", first)
            changed_code = make_identity(("a" * 64, None), "query", second)
        with patch("zebao_rag.identity.importlib.metadata.version", side_effect=lambda name: "2" if name == "peft" else "1"):
            changed_dependency = make_identity(("a" * 64, None), "query", first)
        self.assertNotEqual(base["space_id"], changed_code["space_id"])
        self.assertNotEqual(base["space_id"], changed_dependency["space_id"])

    def test_missing_implementation_or_dependency_is_rejected(self):
        from importlib.metadata import PackageNotFoundError

        with self.assertRaises(IdentityUnavailable):
            implementation_fingerprint({"bad": object()})
        with self.assertRaises(IdentityUnavailable):
            make_identity(("a" * 64, None), "query", "")
        with patch("zebao_rag.identity.importlib.metadata.version", side_effect=PackageNotFoundError):
            with self.assertRaises(IdentityUnavailable):
                make_identity(("a" * 64, None), "query", "f" * 64)

    def test_missing_or_empty_model_is_rejected_without_path_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(IdentityUnavailable) as caught:
                artifact_fingerprints(Path(tmp), None)
            self.assertNotIn(tmp, str(caught.exception))

    def test_symlink_directory_is_rejected_but_file_symlink_tracks_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model, outside = root / "model", root / "outside"
            model.mkdir()
            outside.mkdir()
            actual_file = outside / "weights"
            actual_file.write_bytes(b"weights-v1")
            (model / "weights").symlink_to(actual_file)
            before = artifact_fingerprints(model, None)
            actual_file.write_bytes(b"weights-v2")
            self.assertNotEqual(before, artifact_fingerprints(model, None))
            (model / "hidden-module").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(IdentityUnavailable):
                artifact_fingerprints(model, None)

    def test_dangling_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp)
            (model / "weights").write_bytes(b"base")
            (model / "unresolved").symlink_to(model / "missing")
            with self.assertRaises(IdentityUnavailable):
                artifact_fingerprints(model, None)

    def test_verified_load_pins_local_paths_and_rejects_artifact_mutation(self):
        from zebao_rag import embedder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model, adapter = root / "model", root / "adapter"
            model.mkdir()
            adapter.mkdir()
            weights = model / "weights"
            weights.write_bytes(b"base-v1")
            (adapter / "weights").write_bytes(b"adapter-v1")
            loader = MagicMock()
            loader.cache_info.return_value.currsize = 0

            def changed_during_load():
                self.assertEqual(embedder._WORKER_MODEL_PATH, str(model.resolve()))
                self.assertEqual(embedder._WORKER_ADAPTER_PATH, str(adapter.resolve()))
                weights.write_bytes(b"base-v2")

            loader.side_effect = changed_during_load
            with (
                patch.object(embedder, "MODEL_NAME", str(model)),
                patch.object(embedder, "ADAPTER_PATH", str(adapter)),
                patch.object(embedder, "get_model", loader),
                patch.object(embedder, "encoding_implementation_id", return_value="f" * 64),
                patch.object(embedder, "_WORKER_MODEL_PATH", None),
                patch.object(embedder, "_WORKER_ADAPTER_PATH", None),
                patch("huggingface_hub.snapshot_download", side_effect=AssertionError("network not allowed")),
            ):
                with self.assertRaises(IdentityUnavailable) as caught:
                    embedder.get_verified_identity("instruction")
                self.assertNotIn(tmp, str(caught.exception))

    def test_snapshot_download_is_offline_unless_explicitly_enabled(self):
        from zebao_rag import embedder

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model"
            model.mkdir()
            (model / "weights").write_bytes(b"base")
            for environment, expected_offline in (({}, True), ({"HF_HUB_OFFLINE": "0"}, False)):
                loader = MagicMock()
                loader.cache_info.return_value.currsize = 0
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(embedder, "MODEL_NAME", "example/repo"),
                    patch.object(embedder, "ADAPTER_PATH", ""),
                    patch.object(embedder, "get_model", loader),
                    patch.object(embedder, "encoding_implementation_id", return_value="f" * 64),
                    patch.object(embedder, "_WORKER_MODEL_PATH", None),
                    patch.object(embedder, "_WORKER_ADAPTER_PATH", None),
                    patch("zebao_rag.identity.importlib.metadata.version", return_value="1"),
                    patch("huggingface_hub.snapshot_download", return_value=str(model)) as download,
                ):
                    embedder.get_verified_identity("instruction")
                    download.assert_called_once_with(repo_id="example/repo", local_files_only=expected_offline)

    def test_implementation_mutation_during_loading_fails_closed(self):
        from zebao_rag import embedder

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model"
            model.mkdir()
            (model / "weights").write_bytes(b"base")
            loader = MagicMock()
            loader.cache_info.return_value.currsize = 0
            with (
                patch.object(embedder, "MODEL_NAME", str(model)),
                patch.object(embedder, "ADAPTER_PATH", ""),
                patch.object(embedder, "get_model", loader),
                patch.object(embedder, "encoding_implementation_id", side_effect=["a" * 64, "b" * 64]),
                patch.object(embedder, "_WORKER_MODEL_PATH", None),
                patch.object(embedder, "_WORKER_ADAPTER_PATH", None),
            ):
                with self.assertRaises(IdentityUnavailable):
                    embedder.get_verified_identity("instruction")


if __name__ == "__main__":
    unittest.main()
