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


def test_generate_reads_json_wrapped_in_prose(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()

    async def model_response(_system: str, _user: str, _request_id: str) -> str:
        return (
            "计划如下：\n```json\n"
            '{"plan": ["写函数"], "summary": "完成", '
            '"edits": [{"path": "util.py", "content": "def add(a, b):\\n    return a + b\\n"}]}\n'
            "```\n请审阅。"
        )

    monkeypatch.setattr(code_agent, "_request_model", model_response)
    result = asyncio.run(
        code_agent.generate_code(
            owner, code_agent.CodeGenerateRequest(task="add"), "wrapped"
        )
    )

    assert result.plan == ["写函数"]
    assert result.edits[0].path == "util.py"


def test_plan_only_drops_unexpected_edits(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()

    async def model_response(_system: str, _user: str, _request_id: str) -> str:
        return json.dumps(
            {
                "plan": ["先看接口"],
                "summary": "只做计划",
                "edits": [{"path": "app.py", "content": "print(1)\n"}],
            }
        )

    monkeypatch.setattr(code_agent, "_request_model", model_response)
    result = asyncio.run(
        code_agent.generate_code(
            owner,
            code_agent.CodeGenerateRequest(task="plan", plan_only=True),
            "plan",
        )
    )

    assert result.edits == []
    assert "已忽略" in result.summary


def _configure_code_model(code_agent: ModuleType, **values: object) -> None:
    settings = code_agent.settings
    settings.CODE_API_FORMAT = values.get("api_format", "openai_chat_completions")
    settings.CODE_API_KEY = values.get("key")
    settings.CODE_MODEL = values.get("model")
    settings.CODE_BASE_URL = values.get("base_url")
    settings.DEEPSEEK_API_KEY = values.get("deepseek_key")
    settings.DEEPSEEK_MODEL = values.get("deepseek_model", "deepseek-flash")
    settings.DEEPSEEK_BASE_URL = values.get("deepseek_base", "https://api.deepseek.com")


def test_local_ollama_is_ready_without_a_stored_key(code_agent: ModuleType) -> None:
    _configure_code_model(
        code_agent,
        model="qwen2.5-coder:7b",
        base_url="http://127.0.0.1:11434/v1",
    )
    config = code_agent.get_model_config()
    assert config.configured is True
    assert config.local is True
    assert config.base_url_host == "127.0.0.1"
    assert code_agent._LOCAL_AUTH_PLACEHOLDER not in config.model_dump_json()
    _model, key, base_url = code_agent._model_settings()
    assert base_url == "http://127.0.0.1:11434/v1"
    assert key.get_secret_value() == code_agent._LOCAL_AUTH_PLACEHOLDER


def test_third_party_config_shows_host_without_the_key(code_agent: ModuleType) -> None:
    secret = "sk-third-party-secret"
    _configure_code_model(
        code_agent,
        key=code_agent.SecretStr(secret),
        model="vendor-model",
        base_url="https://user:hidden@api.example.com/v1?token=hidden",
    )
    config = code_agent.get_model_config()
    assert config.configured is True
    assert config.local is False
    assert config.base_url_host == "api.example.com"
    assert secret not in config.model_dump_json()


def test_remote_openai_compatible_without_key_is_not_configured(
    code_agent: ModuleType,
) -> None:
    _configure_code_model(
        code_agent,
        model="vendor-model",
        base_url="https://api.example.com/v1",
    )
    config = code_agent.get_model_config()
    assert config.configured is False
    assert config.base_url_host == "api.example.com"


def test_chat_completions_accepts_text_part_lists(code_agent: ModuleType) -> None:
    text = code_agent._extract_provider_text(
        "openai_chat_completions",
        {"choices": [{"message": {"content": [{"text": "def add():\n    return 1"}]}}]},
    )
    assert "def add" in text


def test_github_repository_parser_rejects_other_hosts(code_agent: ModuleType) -> None:
    owner, name = code_agent.parse_github_repository("https://github.com/octocat/Hello-World.git")
    assert (owner, name) == ("octocat", "Hello-World")
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.parse_github_repository("https://gitlab.com/octocat/Hello-World")
    assert error.value.code == "invalid_github_repository"


def test_github_import_skips_dependencies_and_binaries(code_agent: ModuleType) -> None:
    paths = code_agent.select_github_paths([
        {"path": "src/app.py", "type": "blob", "size": 20},
        {"path": "node_modules/left-pad/index.js", "type": "blob", "size": 20},
        {"path": "assets/logo.png", "type": "blob", "size": 20},
        {"path": "README.md", "type": "blob", "size": 10},
    ])
    assert paths == ["README.md", "src/app.py"]


def test_review_diff_shows_added_and_deleted_lines(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    code_agent.save_workspace_file(owner, "src/app.py", "one\ntwo\n")
    created = code_agent.review_workspace_diff(owner, "src/new.py", "print(1)\n")
    assert created.status == "new"
    assert [line.kind for line in created.lines] == ["add", "add"]
    same = code_agent.review_workspace_diff(owner, "src/app.py", "one\ntwo\n")
    assert same.status == "unchanged"
    assert same.lines == []
    changed = code_agent.review_workspace_diff(owner, "src/app.py", "one\nthree\n")
    assert changed.status == "changed"
    assert ("delete", "two") in [(line.kind, line.text) for line in changed.lines]
    assert ("add", "three") in [(line.kind, line.text) for line in changed.lines]
    long_file = "\n".join(f"line {index}" for index in range(100))
    preview = code_agent.diff_text(None, long_file)
    assert preview.truncated is True
    assert len(preview.lines) == 80
    complete = code_agent.diff_text(None, long_file, full=True)
    assert complete.truncated is False
    assert len(complete.lines) == 100


def test_save_and_delete_stay_inside_the_owner_workspace(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    code_agent.save_workspace_file(owner, "src/app.py", "print(1)\n")
    assert code_agent.read_workspace_file(owner, "src/app.py") == "print(1)\n"
    code_agent.save_workspace_file(owner, "src/app.py", "print(2)\n")
    assert code_agent.read_workspace_file(owner, "src/app.py") == "print(2)\n"
    code_agent.delete_workspace_file(owner, "src/app.py")
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.read_workspace_file(owner, "src/app.py")
    assert error.value.code == "workspace_file_not_found"
    with pytest.raises(code_agent.CodeAgentError) as escaped:
        code_agent.delete_workspace_file(owner, "../escape.py")
    assert escaped.value.code == "invalid_workspace_path"


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
