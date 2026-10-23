"""Fault-inject validation orchestration without Docker, app config, or network."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def runner():
    source = Path(__file__).resolve().parents[2] / "scripts/verify-isolated.py"
    spec = importlib.util.spec_from_file_location("isolated_validation_runner", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(code=0, output=""):
    return SimpleNamespace(returncode=code, stdout=output)


def test_cleanup_never_removes_a_container_with_another_label(runner, monkeypatch):
    execute = MagicMock(return_value=result(output="container-id someone-else"))
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert not runner.cleanup_container("validation-name", "our-run")
    assert execute.call_count == 1


def test_stop_timeout_falls_back_to_the_verified_id(runner, monkeypatch):
    commands = []
    removed = False

    def execute(command, **_kwargs):
        nonlocal removed
        commands.append(command)
        if command[1:2] == ["inspect"]:
            return result(output="stable-container-id our-run")
        if command[1:2] == ["stop"]:
            raise subprocess.TimeoutExpired(command, 15)
        if command[1:3] == ["container", "inspect"]:
            return result(code=1 if removed else 0)
        if command[1:2] == ["rm"]:
            assert command == ["docker", "rm", "--force", "stable-container-id"]
            removed = True
        return result()

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.cleanup_container("validation-name", "our-run")
    assert removed


def test_disconnected_daemon_does_not_count_as_confirmed_cleanup(runner, monkeypatch):
    execute = MagicMock(return_value=result(code=1))
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert not runner.cleanup_container("validation-name", "our-run")
    assert not any(call.args[0][1:2] == ["rm"] for call in execute.call_args_list)


def test_api_termination_error_is_reported_without_raising(runner):
    process = MagicMock()
    process.poll.return_value = None
    process.terminate.side_effect = OSError("process handle unavailable")
    assert not runner.stop_api(process)


def test_creation_timeout_still_cleans_temp_files_and_writes_failure_summary(
    runner, monkeypatch, tmp_path
):
    envfiles = []
    cleanup = MagicMock(return_value=False)

    def execute(command, **_kwargs):
        if command[1:2] == ["run"]:
            envfiles.append(Path(command[command.index("--env-file") + 1]))
            raise subprocess.TimeoutExpired(command, 120)
        return result(output="cached")

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "cleanup_container", cleanup)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    monkeypatch.setattr(sys, "argv", ["verify-isolated.py", "--skip-runtime"])
    assert runner.main() == 1
    cleanup.assert_called_once()
    assert envfiles and not envfiles[0].exists()
    summary_path = next(
        (tmp_path / ".local/isolated-validation").glob("*/summary.json")
    )
    summary = json.loads(summary_path.read_text())
    assert summary["cleanup"] == {
        "container_stopped": False,
        "temporary_directory_removed": True,
    }
    assert "error" in summary
    assert summary_path.stat().st_mode & 0o777 == 0o600
