"""Hold one PostgreSQL session lock before recovering or running AIGC jobs."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

# Stable application-specific key, shared by all API processes using this database.
AIGC_RUNNER_LOCK_KEY = 0x44455641494743


@contextmanager
def exclusive_aigc_runner(engine: Engine) -> Iterator[None]:
    """Reject another runner without touching its running jobs.

    This is a session lock, so the dedicated connection must remain checked out
    until the runner has actually stopped. PostgreSQL releases it on disconnect.
    """
    with engine.connect() as connection:
        try:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": AIGC_RUNNER_LOCK_KEY},
            ).scalar_one()
            connection.commit()
        except BaseException:
            connection.invalidate()
            raise
        if not acquired:
            raise RuntimeError(
                "AIGC 执行器已在其他进程运行；请使用单 worker、单实例启动，"
                "并等待原实例退出后再重启。"
            )
        try:
            yield
        finally:
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": AIGC_RUNNER_LOCK_KEY},
                )
                connection.commit()
            except BaseException:
                # A pooled session must never retain the lock after failed cleanup.
                connection.invalidate()
                raise
