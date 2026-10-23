"""Filesystem-only workspace checks; run with pytest --noconftest.

The isolated module uses a temporary workspace and never loads application settings,
database fixtures, provider credentials, or the real project's workspace files.
"""

import asyncio
import importlib.util
import json
import os
import stat
import sys
import threading
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def code_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    config = ModuleType("app.core.config")
    config.settings = SimpleNamespace(CODE_WORKSPACE_DIR=tmp_path / "workspaces")
    source = Path(__file__).resolve().parents[1] / "app" / "code_agent.py"
    spec = importlib.util.spec_from_file_location("isolated_code_agent", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, "app.core.config", config)
        spec.loader.exec_module(module)
    return module


def workspace_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_applies_existing_and_new_files(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "existing.py").write_text("before\n")

    result = code_agent.apply_workspace_edits(
        owner,
        [
            code_agent.CodeFileEdit(path="existing.py", content="after\n"),
            code_agent.CodeFileEdit(path="nested/new.py", content="新增内容\n"),
        ],
    )

    assert workspace_files(root) == {
        "existing.py": b"after\n",
        "nested/new.py": "新增内容\n".encode(),
    }
    assert {item.path for item in result.files} == {"existing.py", "nested/new.py"}
    assert result.total_bytes == len(b"after\n") + len("新增内容\n".encode())
    assert stat.S_IMODE((root / "existing.py").stat().st_mode) == 0o600


@pytest.mark.parametrize("failure_number", [1, 2, 3])
def test_failed_batch_restores_original_bytes_and_removes_new_files(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch, failure_number: int
) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    original = b"\xef\xbb\xbfbefore\r\n"
    existing = root / "existing.py"
    existing.write_bytes(original)
    existing.chmod(0o640)
    (root / "last.py").write_text("last before\n")
    before = workspace_files(root)
    replace = os.replace
    calls = 0

    def fail_one_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_number:
            raise OSError("injected file replacement failure")
        replace(source, destination)

    monkeypatch.setattr(code_agent.os, "replace", fail_one_replace)
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(
            owner,
            [
                code_agent.CodeFileEdit(path="existing.py", content="changed"),
                code_agent.CodeFileEdit(path="nested/new.py", content="new"),
                code_agent.CodeFileEdit(path="last.py", content="last changed"),
            ],
        )

    assert error.value.code == "workspace_write_failed"
    assert workspace_files(root) == before
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640


def test_staging_failure_cleans_temporary_files_without_applying_edits(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "first.py").write_text("first before")
    (root / "second.py").write_text("second before")
    before = workspace_files(root)
    read_bytes = Path.read_bytes

    def fail_read(path: Path) -> bytes:
        if path == root / "second.py":
            raise OSError("injected backup read failure")
        return read_bytes(path)

    with monkeypatch.context() as failure:
        failure.setattr(Path, "read_bytes", fail_read)
        with pytest.raises(code_agent.CodeAgentError) as error:
            code_agent.apply_workspace_edits(
                owner,
                [
                    code_agent.CodeFileEdit(path="first.py", content="changed"),
                    code_agent.CodeFileEdit(path="second.py", content="changed"),
                ],
            )
    assert error.value.code == "workspace_write_failed"
    assert workspace_files(root) == before


def test_failed_rollback_reports_partial_state_and_keeps_original_backup(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "first.py").write_text("first original")
    (root / "last.py").write_text("last original")
    replace = os.replace
    calls = 0

    def fail_apply_and_one_restore(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls in (3, 4):
            raise OSError("injected apply or rollback failure")
        replace(source, destination)

    monkeypatch.setattr(code_agent.os, "replace", fail_apply_and_one_restore)
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(
            owner,
            [
                code_agent.CodeFileEdit(path="new.py", content="new"),
                code_agent.CodeFileEdit(path="first.py", content="changed"),
                code_agent.CodeFileEdit(path="last.py", content="last changed"),
            ],
        )

    assert error.value.code == "workspace_rollback_failed"
    assert (root / "first.py").read_text() == "changed"
    assert (root / "last.py").read_text() == "last original"
    assert not (root / "new.py").exists()
    backups = [path for path in root.iterdir() if path.name not in {"first.py", "last.py"}]
    assert len(backups) == 1 and backups[0].read_text() == "first original"
    assert backups[0].name.startswith(".first.py.rollback-")
    assert {item.path for item in code_agent.list_workspace(owner).files} == {
        "first.py", "last.py"
    }


def test_generate_reads_context_outside_event_loop(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "main.py").write_text("print('context')")
    event_loop_thread = threading.get_ident()
    read_threads: list[int] = []
    read_file = code_agent.read_workspace_file

    def record_read(owner_id: uuid.UUID, path: str) -> str:
        read_threads.append(threading.get_ident())
        return read_file(owner_id, path)

    async def model_response(_system: str, user: str, _request_id: str) -> str:
        assert "print('context')" in user
        assert threading.get_ident() == event_loop_thread
        return json.dumps({"plan": ["review"], "summary": "ready", "edits": []})

    monkeypatch.setattr(code_agent, "read_workspace_file", record_read)
    monkeypatch.setattr(code_agent, "_request_model", model_response)
    result = asyncio.run(
        code_agent.generate_code(
            owner,
            code_agent.CodeGenerateRequest(task="review", workspace_paths=["main.py"]),
            "test-request",
        )
    )

    assert result.summary == "ready"
    assert read_threads and all(thread != event_loop_thread for thread in read_threads)


@pytest.mark.parametrize("invalid_path", ["../escape.py", "/escape.py", ".hidden.py"])
def test_batch_still_rejects_unsafe_paths(
    code_agent: ModuleType, invalid_path: str
) -> None:
    owner = uuid.uuid4()
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(
            owner, [code_agent.CodeFileEdit(path=invalid_path, content="unsafe")]
        )
    assert error.value.code == "invalid_workspace_path"
