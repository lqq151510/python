"""Filesystem-only workspace checks; run with pytest --noconftest.

The isolated module uses a temporary workspace and never loads application settings,
database fixtures, provider credentials, or the real project's workspace files.
"""

import asyncio
import importlib.util
import json
import multiprocessing
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


def make_edit(code_agent: ModuleType, owner: uuid.UUID, *, path: str, content: str):
    version = code_agent.review_workspace_diff(owner, path, content).base_version
    return code_agent.CodeApplyEdit(path=path, content=content, base_version=version)


def test_applies_existing_and_new_files(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "existing.py").write_text("before\n")

    result = code_agent.apply_workspace_edits(
        owner,
        [
            make_edit(code_agent, owner, path="existing.py", content="after\n"),
            make_edit(code_agent, owner, path="nested/new.py", content="新增内容\n"),
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
                make_edit(code_agent, owner, path="existing.py", content="changed"),
                make_edit(code_agent, owner, path="nested/new.py", content="new"),
                make_edit(code_agent, owner, path="last.py", content="last changed"),
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
                    make_edit(code_agent, owner, path="first.py", content="changed"),
                    make_edit(code_agent, owner, path="second.py", content="changed"),
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
                make_edit(code_agent, owner, path="new.py", content="new"),
                make_edit(code_agent, owner, path="first.py", content="changed"),
                make_edit(code_agent, owner, path="last.py", content="last changed"),
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


@pytest.mark.parametrize("failure_stage", ["metadata", "tree", "file"])
@pytest.mark.parametrize("timeout", [False, True])
def test_github_network_failures_are_classified(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch, failure_stage: str, timeout: bool
) -> None:
    httpx = code_agent.httpx
    client_class = httpx.AsyncClient
    monkeypatch.setattr(code_agent.settings, "GITHUB_TOKEN", None, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        stage = "file" if request.url.host == "raw.githubusercontent.com" else (
            "tree" if "/git/trees/" in request.url.path else "metadata"
        )
        if stage == failure_stage:
            error = httpx.ReadTimeout if timeout else httpx.ConnectError
            raise error("sensitive underlying details", request=request)
        if stage == "metadata":
            return httpx.Response(200, json={"private": False, "default_branch": "main"})
        return httpx.Response(200, json={"tree": [{"type": "blob", "path": "a.py", "size": 2}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    with pytest.raises(code_agent.CodeAgentError) as error:
        asyncio.run(code_agent.import_github_repository(uuid.uuid4(), "owner/repo"))
    assert error.value.status_code == (504 if timeout else 502)
    assert error.value.code == ("github_timeout" if timeout else "github_unavailable")
    assert "sensitive" not in error.value.message


def test_github_invalid_metadata_json_is_classified(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx = code_agent.httpx
    client_class = httpx.AsyncClient
    monkeypatch.setattr(code_agent.settings, "GITHUB_TOKEN", None, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not json")),
        **kwargs,
    ))
    with pytest.raises(code_agent.CodeAgentError) as error:
        asyncio.run(code_agent.import_github_repository(uuid.uuid4(), "owner/repo"))
    assert error.value.status_code == 502
    assert error.value.code == "github_unavailable"


def test_github_import_reports_only_files_written(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx = code_agent.httpx
    client_class = httpx.AsyncClient
    monkeypatch.setattr(code_agent.settings, "GITHUB_TOKEN", None, raising=False)
    monkeypatch.setattr(code_agent, "MAX_WORKSPACE_FILES", 2)
    owner = uuid.uuid4()
    code_agent.save_workspace_file(owner, "existing.py", "original", "missing")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="new")
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": [
                {"type": "blob", "path": name, "size": 3}
                for name in ("existing.py", "new.py", "other.py")
            ]})
        return httpx.Response(200, json={"private": False, "default_branch": "main"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    result = asyncio.run(code_agent.import_github_repository(owner, "owner/repo"))
    assert result.imported == 1
    assert {item.path for item in result.files} == {"existing.py", "new.py"}
    assert code_agent.read_workspace_file(owner, "existing.py") == "original"


def test_review_diff_shows_added_and_deleted_lines(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    code_agent.save_workspace_file(owner, "src/app.py", "one\ntwo\n", "missing")
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


def test_diff_budget_limits_repeated_lines_before_matching(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    repeated = "same\n" * 2000
    before = repeated + "old"
    after = repeated + "new"

    def unexpected_matcher(*_args: object, **_kwargs: object) -> None:
        pytest.fail("oversized diff must not invoke SequenceMatcher")

    monkeypatch.setattr(code_agent.difflib, "SequenceMatcher", unexpected_matcher)
    for full in (False, True):
        with pytest.raises(code_agent.CodeAgentError) as error:
            code_agent.diff_text(before, after, full=full)
        assert error.value.status_code == 422
        assert error.value.code == "diff_too_complex"
        assert "缩小" in error.value.message
        assert code_agent.diff_text(before, before, full=full).status == "unchanged"
        assert code_agent.diff_text(None, after, full=full).status == "new"


def test_diff_budget_boundary(code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_agent, "MAX_DIFF_LINE_COMPARISONS", 9)
    result = code_agent.diff_text("a\nb\nc", "a\nb\nd")
    assert result.status == "changed"
    assert [(line.kind, line.text) for line in result.lines] == [("delete", "c"), ("add", "d")]
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.diff_text("a\nb\nc", "a\nb\nc\nd")
    assert error.value.code == "diff_too_complex"


def test_save_and_delete_stay_inside_the_owner_workspace(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    code_agent.save_workspace_file(owner, "src/app.py", "print(1)\n", "missing")
    assert code_agent.read_workspace_file(owner, "src/app.py") == "print(1)\n"
    _, version = code_agent.read_workspace_file_with_version(owner, "src/app.py")
    code_agent.save_workspace_file(owner, "src/app.py", "print(2)\n", version)
    assert code_agent.read_workspace_file(owner, "src/app.py") == "print(2)\n"
    code_agent.delete_workspace_file(owner, "src/app.py")
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.read_workspace_file(owner, "src/app.py")
    assert error.value.code == "workspace_file_not_found"
    with pytest.raises(code_agent.CodeAgentError) as escaped:
        code_agent.delete_workspace_file(owner, "../escape.py")
    assert escaped.value.code == "invalid_workspace_path"


def test_manual_save_uses_raw_bytes_version_and_detects_changes(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    file = root / "file.py"
    file.write_bytes(b"\xef\xbb\xbfline\r\n")
    content, version = code_agent.read_workspace_file_with_version(owner, "file.py")
    assert content == "line\n"
    assert version == code_agent._snapshot_version(b"\xef\xbb\xbfline\r\n")
    # The displayed text is unchanged, but its bytes have changed.
    file.write_bytes(b"line\n")
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.save_workspace_file(owner, "file.py", "draft", version)
    assert error.value.status_code == 409
    assert error.value.code == "workspace_conflict"
    assert file.read_bytes() == b"line\n"
    _, fresh_version = code_agent.read_workspace_file_with_version(owner, "file.py")
    code_agent.save_workspace_file(owner, "file.py", "draft", fresh_version)
    assert file.read_bytes() == b"draft"


def test_manual_save_rejects_creation_and_deletion_races(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    file = root / "file.py"
    file.write_bytes(b"")
    _, version = code_agent.read_workspace_file_with_version(owner, "file.py")
    assert version != "missing"
    with pytest.raises(code_agent.CodeAgentError) as creation:
        code_agent.save_workspace_file(owner, "file.py", "draft", "missing")
    assert creation.value.code == "workspace_conflict"
    assert file.read_bytes() == b""
    file.unlink()
    with pytest.raises(code_agent.CodeAgentError) as deletion:
        code_agent.save_workspace_file(owner, "file.py", "draft", version)
    assert deletion.value.code == "workspace_conflict"
    assert not file.exists()


def test_manual_saves_from_two_processes_allow_only_one(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "file.py").write_bytes(b"old")
    _, version = code_agent.read_workspace_file_with_version(owner, "file.py")
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()

    def save(value: str) -> None:
        start.wait(5)
        try:
            code_agent.save_workspace_file(owner, "file.py", value, version)
            results.put("ok")
        except code_agent.CodeAgentError as exc:
            results.put(exc.code)

    processes = [context.Process(target=save, args=(value,)) for value in ("one", "two")]
    for process in processes:
        process.start()
    try:
        start.set()
        assert sorted(results.get(timeout=10) for _ in processes) == ["ok", "workspace_conflict"]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert (root / "file.py").read_text() in {"one", "two"}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        results.close()


@pytest.mark.parametrize("invalid_path", ["../escape.py", "/escape.py", ".hidden.py"])
def test_batch_still_rejects_unsafe_paths(
    code_agent: ModuleType, invalid_path: str
) -> None:
    owner = uuid.uuid4()
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(
            owner, [code_agent.CodeApplyEdit(path=invalid_path, content="unsafe", base_version="missing")]
        )
    assert error.value.code == "invalid_workspace_path"


@pytest.mark.parametrize("change", ["modify", "delete", "create"])
def test_version_conflict_rejects_entire_batch(code_agent: ModuleType, change: str) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "unchanged.py").write_text("keep")
    target = root / "raced.py"
    if change != "create":
        target.write_text("original")
    edits = [
        make_edit(code_agent, owner, path="unchanged.py", content="replacement"),
        make_edit(code_agent, owner, path="raced.py", content="candidate"),
    ]
    if change == "delete":
        target.unlink()
    else:
        target.write_text("external change")
    before = workspace_files(root)
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(owner, edits)
    assert error.value.status_code == 409
    assert error.value.code == "workspace_conflict"
    assert workspace_files(root) == before


def test_versions_distinguish_raw_bytes_and_missing(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    target = root / "file.py"
    missing = code_agent.review_workspace_diff(owner, "file.py", "").base_version
    target.write_bytes(b"")
    empty = code_agent.review_workspace_diff(owner, "file.py", "").base_version
    assert missing == "missing"
    assert empty.startswith("sha256:") and empty != missing
    target.write_bytes(b"\xef\xbb\xbfline\r\n")
    first = code_agent.review_workspace_diff(owner, "file.py", "line\n")
    assert first.status == "unchanged"
    target.write_bytes(b"line\n")
    second = code_agent.review_workspace_diff(owner, "file.py", "line\n")
    assert second.status == "unchanged"
    assert first.base_version != second.base_version
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.apply_workspace_edits(owner, [code_agent.CodeApplyEdit(
            path="file.py", content="changed", base_version=first.base_version
        )])
    assert error.value.code == "workspace_conflict"
    assert target.read_bytes() == b"line\n"


def test_apply_request_rejects_missing_or_invalid_versions(code_agent: ModuleType) -> None:
    from pydantic import ValidationError

    for version in (None, "", "sha256:123"):
        edit = {"path": "file.py", "content": "new"}
        if version is not None:
            edit["base_version"] = version
        with pytest.raises(ValidationError):
            code_agent.CodeApplyRequest(permission="workspace_modify", approved=True, edits=[edit])


def test_two_processes_cannot_apply_same_version(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    (root / "race.py").write_text("original")
    version = code_agent.review_workspace_diff(owner, "race.py", "first").base_version
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()

    def run(content: str) -> None:
        start.wait(5)
        try:
            code_agent.apply_workspace_edits(owner, [code_agent.CodeApplyEdit(
                path="race.py", content=content, base_version=version
            )])
            results.put("ok")
        except code_agent.CodeAgentError as exc:
            results.put(exc.code)

    processes = [context.Process(target=run, args=(name,)) for name in ("first", "second")]
    for process in processes:
        process.start()
    start.set()
    try:
        observed = sorted(results.get(timeout=10) for _ in processes)
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert observed == ["ok", "workspace_conflict"]
        assert (root / "race.py").read_text() in {"first", "second"}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        results.close()


def test_lock_directory_symlink_is_rejected(code_agent: ModuleType, tmp_path: Path) -> None:
    base = code_agent.settings.CODE_WORKSPACE_DIR
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / ".locks").symlink_to(outside, target_is_directory=True)
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.save_workspace_file(uuid.uuid4(), "file.py", "text", "missing")
    assert error.value.code == "workspace_unavailable"
    assert list(outside.iterdir()) == []


def test_lock_retries_transient_missing_lock_file(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()
    original_open = code_agent.os.open
    attempts = 0

    def open_once_missing(path, flags, *args, **kwargs):
        nonlocal attempts
        if str(path) == f"{owner}.lock":
            attempts += 1
            if attempts == 1:
                raise FileNotFoundError("simulated simultaneous first open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(code_agent.os, "open", open_once_missing)
    code_agent.save_workspace_file(owner, "file.py", "text", "missing")
    assert attempts == 2
    assert code_agent.read_workspace_file(owner, "file.py") == "text"


def test_lock_failure_after_retries_does_not_write(
    code_agent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    original_open = code_agent.os.open
    attempts = 0

    def open_always_missing(path, flags, *args, **kwargs):
        nonlocal attempts
        if str(path) == f"{owner}.lock":
            attempts += 1
            raise FileNotFoundError("simulated persistent lock failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(code_agent.os, "open", open_always_missing)
    with pytest.raises(code_agent.CodeAgentError) as error:
        code_agent.save_workspace_file(owner, "file.py", "text", "missing")
    assert error.value.code == "workspace_unavailable"
    assert attempts == 3
    assert not (root / "file.py").exists()


def test_manual_save_waits_for_other_process_workspace_lock(code_agent: ModuleType) -> None:
    owner = uuid.uuid4()
    root = code_agent._owner_root(owner)
    context = multiprocessing.get_context("fork")
    locked = context.Event()
    release = context.Event()
    saved = context.Event()

    def hold_lock() -> None:
        with code_agent._workspace_writer(owner):
            locked.set()
            release.wait(5)

    def save() -> None:
        code_agent.save_workspace_file(owner, "file.py", "manual edit", "missing")
        saved.set()

    holder = context.Process(target=hold_lock)
    writer = context.Process(target=save)
    holder.start()
    try:
        assert locked.wait(5)
        writer.start()
        assert not saved.wait(0.2)
        assert not (root / "file.py").exists()
        release.set()
        assert saved.wait(5)
        holder.join(5)
        writer.join(5)
        assert holder.exitcode == writer.exitcode == 0
        assert (root / "file.py").read_text() == "manual edit"
    finally:
        release.set()
        for process in (holder, writer):
            if process.is_alive():
                process.terminate()
                process.join()
