"""Verify the unified CLI launcher without importing models or opening data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_PYTHON = r"""
import json
import os
import sys
from pathlib import Path

Path(os.environ["FAKE_LOG"]).write_text(json.dumps({
    "executable": sys.argv[0],
    "args": sys.argv[1:],
    "cwd": str(Path.cwd()),
    "pythonpath": os.environ.get("PYTHONPATH"),
    "offline": os.environ.get("HF_HUB_OFFLINE"),
    "data_dir": os.environ.get("ZEBAO_RAG_DATA_DIR"),
    "chroma_dir": os.environ.get("ZEBAO_RAG_CHROMA_DIR"),
}))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


class RagCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="devassist-rag-cli-")
        self.addCleanup(temporary.cleanup)
        self.outside = Path(temporary.name).resolve()
        self.root = self.outside / "project with spaces"
        (self.root / "scripts").mkdir(parents=True)
        self.script = self.root / "scripts/rag-cli.sh"
        shutil.copy2(REPO_ROOT / "scripts/rag-cli.sh", self.script)
        self.service = self.root / "services/zebao-rag"
        self.python = self.service / ".venv/bin/python"
        self.make_interpreter(self.python)
        self.log = self.outside / "calls.json"
        self.env = {"PATH": os.defpath, "FAKE_LOG": str(self.log)}

    @staticmethod
    def make_interpreter(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{FAKE_PYTHON}")
        path.chmod(0o700)

    def run_cli(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), *args],
            cwd=self.outside,
            env={**self.env, **env},
            text=True,
            capture_output=True,
            check=False,
        )

    def test_help_does_not_start_python_or_create_data(self) -> None:
        self.python.unlink()
        for args in [(), ("--help",), ("-h",)]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("DevAssist 本地 RAG", result.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.service / "data").exists())
        self.assertFalse((self.service / "chroma_db").exists())

    def test_pins_project_source_and_preserves_arguments_from_other_directory(self) -> None:
        args = ("code", "保留 空格", "literal $(do-not-run)", "--example")
        result = self.run_cli(*args, PYTHONPATH="/synthetic/other-source")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(self.log.read_text())
        self.assertEqual(call["executable"], str(self.python))
        self.assertEqual(call["args"], ["-m", "zebao_rag.cli", *args])
        self.assertEqual(call["cwd"], str(self.root))
        self.assertEqual(
            call["pythonpath"], f"{self.service}/src:/synthetic/other-source"
        )
        self.assertEqual(call["offline"], "1")

    def test_explicit_interpreter_environment_and_exit_code_are_preserved(self) -> None:
        alternate = self.outside / "alternate python"
        self.make_interpreter(alternate)
        result = self.run_cli(
            "sessions",
            RAG_PYTHON=str(alternate),
            HF_HUB_OFFLINE="0",
            ZEBAO_RAG_DATA_DIR="/synthetic/data",
            ZEBAO_RAG_CHROMA_DIR="/synthetic/chroma",
            FAKE_EXIT="19",
        )
        self.assertEqual(result.returncode, 19, result.stderr)
        call = json.loads(self.log.read_text())
        self.assertEqual(call["executable"], str(alternate))
        self.assertEqual(call["offline"], "0")
        self.assertEqual(call["data_dir"], "/synthetic/data")
        self.assertEqual(call["chroma_dir"], "/synthetic/chroma")
        self.assertEqual(call["pythonpath"], str(self.service / "src"))

    def test_missing_environment_fails_without_installing(self) -> None:
        self.python.unlink()
        result = self.run_cli("sessions")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("uv sync --project services/zebao-rag --frozen --python 3.12", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.python.exists())

    def test_relative_interpreter_is_rejected(self) -> None:
        result = self.run_cli("sessions", RAG_PYTHON="python")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("绝对路径", result.stderr)
        self.assertFalse(self.log.exists())

    def test_non_executable_or_directory_is_rejected(self) -> None:
        self.python.chmod(0o600)
        for path in [self.python, self.python.parent]:
            with self.subTest(path=path):
                result = self.run_cli("sessions", RAG_PYTHON=str(path))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("不可执行", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
