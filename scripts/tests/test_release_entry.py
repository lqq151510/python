"""Check release command ordering and failure boundaries without real Docker."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_DOCKER = r"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
for name in os.environ.get("FAKE_FORBIDDEN_ENV", "").split(","):
    if name and name in os.environ:
        sys.exit(96)
if os.environ.get("FAKE_EXPECT_PROXY") and os.environ.get("HTTPS_PROXY") != "synthetic-proxy":
    sys.exit(95)
root = Path(os.environ["FAKE_ROOT"])
expected = ["compose", "--project-name", os.environ.get("FAKE_PROJECT", "devassist-release"),
            "--env-file", str(root / "private release.env"), "-f", str(root / "compose.release.yml")]
if args[:7] != expected or Path.cwd() != root:
    sys.exit(97)
commands = {
    ("config", "--quiet"): "config",
    ("build", "backend"): "build",
    ("up", "-d", "--wait", "--wait-timeout", "120", "db"): "db",
    ("stop", "backend"): "stop_backend",
    ("run", "--rm", "--no-deps", "prestart"): "prestart",
    ("up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "backend"): "backend",
    ("ps",): "status",
    ("stop", "backend", "db"): "stop",
}
event = commands.get(tuple(args[7:]))
if not event:
    sys.exit(98)
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(event) + "\n")
if event == os.environ.get("FAKE_FAIL"):
    sys.exit(19)
"""


class ReleaseEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="devassist-release-mock-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "scripts").mkdir()
        shutil.copy2(
            REPO_ROOT / "scripts/devassist-release.sh",
            self.root / "scripts/devassist-release.sh",
        )
        (self.root / "compose.release.yml").write_text("services: {}\n")
        self.env_file = self.root / "private release.env"
        # Sourcing this file would create a marker and expose a fake secret.
        self.env_file.write_text("SECRET_KEY=synthetic-secret\ntouch sourced-marker\n")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake = fake_bin / "docker"
        fake.write_text(f"#!{sys.executable}\n{FAKE_DOCKER}")
        fake.chmod(0o700)
        self.env = {
            "PATH": str(fake_bin) + os.pathsep + os.defpath,
            "FAKE_ROOT": str(self.root),
        }

    def run_script(
        self, *args: str, failure: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.root / "scripts/devassist-release.sh"), *args],
            cwd=self.root,
            env={**self.env, "FAKE_FAIL": failure},
            text=True,
            capture_output=True,
            check=False,
        )

    def events(self) -> list[str]:
        log = self.root / "calls.jsonl"
        return (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )

    def test_up_orders_build_health_migration_and_start(self) -> None:
        result = self.run_script("--env-file", self.env_file.name, "up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.events(),
            ["config", "build", "db", "stop_backend", "prestart", "backend", "status"],
        )
        self.assertFalse((self.root / "sourced-marker").exists())
        self.assertNotIn("synthetic-secret", result.stdout + result.stderr)

    def test_failure_stops_all_following_steps(self) -> None:
        sequence = [
            "config",
            "build",
            "db",
            "stop_backend",
            "prestart",
            "backend",
            "status",
        ]
        for index, event in enumerate(sequence):
            with self.subTest(event=event):
                (self.root / "calls.jsonl").unlink(missing_ok=True)
                result = self.run_script(
                    "--env-file", str(self.env_file), "up", failure=event
                )
                self.assertEqual(result.returncode, 19)
                self.assertEqual(self.events(), sequence[: index + 1])

    def test_stop_preserves_containers_and_volumes(self) -> None:
        result = self.run_script("--env-file", str(self.env_file), "stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), ["config", "stop"])

    def test_check_build_and_status_are_scoped(self) -> None:
        for action in ("check", "build", "status"):
            with self.subTest(action=action):
                (self.root / "calls.jsonl").unlink(missing_ok=True)
                result = self.run_script("--env-file", str(self.env_file), action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.events(), ["config"] + ([] if action == "check" else [action])
                )

    def test_missing_env_and_invalid_options_do_not_call_docker(self) -> None:
        for args in (
            ("up",),
            ("--env-file",),
            ("--env-file", "missing.env", "up"),
            ("--env-file", str(self.env_file), "down"),
            ("--env-file", str(self.env_file), "up", "stop"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)
                self.assertEqual(self.events(), [])

    def test_explicit_file_wins_over_exported_application_settings(self) -> None:
        variables = set(
            re.findall(
                r"\$\{([A-Z_]+)", (REPO_ROOT / "compose.release.yml").read_text()
            )
        )
        self.env.update({name: "synthetic-shell-override" for name in variables})
        self.env["FAKE_FORBIDDEN_ENV"] = ",".join(sorted(variables))
        self.env["HTTPS_PROXY"] = "synthetic-proxy"
        self.env["FAKE_EXPECT_PROXY"] = "1"
        result = self.run_script("--env-file", str(self.env_file), "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), ["config"])
        self.assertNotIn("synthetic-shell-override", result.stdout + result.stderr)

    def test_rejects_existing_development_project(self) -> None:
        result = self.run_script(
            "--env-file", str(self.env_file), "--project-name", "devassist", "up"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.events(), [])

    def test_explicit_isolated_project(self) -> None:
        self.env["FAKE_PROJECT"] = "devassist-release-smoke123"
        result = self.run_script(
            "--env-file",
            str(self.env_file),
            "--project-name",
            self.env["FAKE_PROJECT"],
            "check",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), ["config"])


if __name__ == "__main__":
    unittest.main()
