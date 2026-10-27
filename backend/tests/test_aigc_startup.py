"""Offline runner ownership and lifespan ordering; no database or ComfyUI calls."""

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.core.aigc_lock import AIGC_RUNNER_LOCK_KEY, exclusive_aigc_runner


def lock_engine(*, acquired: bool = True):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = acquired
    return engine, connection


def test_lock_is_held_until_the_guarded_runner_exits():
    engine, connection = lock_engine()
    with exclusive_aigc_runner(engine):
        assert connection.execute.call_count == 1
        assert "pg_try_advisory_lock" in str(connection.execute.call_args.args[0])
        assert connection.execute.call_args.args[1] == {"key": AIGC_RUNNER_LOCK_KEY}
    assert connection.execute.call_count == 2
    assert "pg_advisory_unlock" in str(connection.execute.call_args.args[0])
    connection.invalidate.assert_not_called()


def test_second_runner_is_rejected_before_running_guarded_code():
    engine, connection = lock_engine(acquired=False)
    entered = False
    with pytest.raises(RuntimeError, match="单 worker"):
        with exclusive_aigc_runner(engine):
            entered = True
    assert not entered
    assert connection.execute.call_count == 1


def test_runner_error_still_releases_the_session_lock():
    engine, connection = lock_engine()
    with pytest.raises(ValueError, match="runner failed"):
        with exclusive_aigc_runner(engine):
            raise ValueError("runner failed")
    assert "pg_advisory_unlock" in str(connection.execute.call_args.args[0])


@pytest.mark.parametrize("stage", ["acquire", "commit", "release"])
def test_lock_errors_discard_connection_instead_of_leaking_a_pooled_lock(stage):
    engine, connection = lock_engine()
    error = RuntimeError("database unavailable")
    if stage == "acquire":
        connection.execute.side_effect = error
    elif stage == "commit":
        connection.commit.side_effect = error
    else:
        connection.execute.side_effect = [MagicMock(), error]
    with pytest.raises(RuntimeError, match="database unavailable"):
        with exclusive_aigc_runner(engine):
            pass
    connection.invalidate.assert_called_once()


@pytest.mark.parametrize("enabled", [True, False])
def test_lifespan_owns_runner_before_recovery_and_until_shutdown(monkeypatch, enabled):
    from app import main

    events = []

    @contextmanager
    def guard(_engine):
        events.append("acquire")
        try:
            yield
        finally:
            events.append("release")

    monkeypatch.setattr(main.settings, "AIGC_ENABLED", enabled)
    monkeypatch.setattr(main, "exclusive_aigc_runner", guard)
    monkeypatch.setattr(main, "Session", MagicMock())
    monkeypatch.setattr(
        main.aigc_runner,
        "recover_interrupted_jobs",
        lambda _session: events.append("recover"),
    )
    monkeypatch.setattr(
        main.aigc_runner, "ensure_runner_started", lambda: events.append("start")
    )
    monkeypatch.setattr(main.aigc_runner, "stop_runner", lambda: events.append("stop"))

    async def exercise():
        async with main.lifespan(main.app):
            events.append("serve")

    asyncio.run(exercise())
    expected = ["acquire", "recover", "start", "serve", "stop", "release"]
    assert events == (expected if enabled else ["serve", "stop"])


def test_rejected_lifespan_never_recovers_or_starts_jobs(monkeypatch):
    from app import main

    @contextmanager
    def rejected(_engine):
        raise RuntimeError("runner already owned")
        yield  # pragma: no cover - contextmanager protocol

    recover, start = MagicMock(), MagicMock()
    monkeypatch.setattr(main.settings, "AIGC_ENABLED", True)
    monkeypatch.setattr(main, "exclusive_aigc_runner", rejected)
    monkeypatch.setattr(main.aigc_runner, "recover_interrupted_jobs", recover)
    monkeypatch.setattr(main.aigc_runner, "ensure_runner_started", start)

    async def exercise():
        async with main.lifespan(main.app):
            pytest.fail("a rejected runner must never serve requests")

    with pytest.raises(RuntimeError, match="runner already owned"):
        asyncio.run(exercise())
    recover.assert_not_called()
    start.assert_not_called()
