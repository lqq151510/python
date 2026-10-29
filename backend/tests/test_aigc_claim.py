"""AIGC 执行器领取任务的语义：最早排队优先、只领 queued、跳过被锁定的行。

真实的 `FOR UPDATE SKIP LOCKED` 只有 PostgreSQL 生效；SQLite 会静默省略该子句，
所以这里用 SQLite 验证状态转换与排序，用编译后的 SQL 验证锁子句，最后在
PostgreSQL 上验证「别的事务持有行锁时不会阻塞、直接领下一条」。
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app import aigc, aigc_runner
from app.models import AigcJob
from tests.utils.user import create_random_user

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
BASE_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as value:
            yield value
    finally:
        engine.dispose()


def _job(
    session: Session,
    *,
    status: str = aigc.STATUS_QUEUED,
    created_at: datetime = BASE_TIME,
    prompt: str = "demo",
    owner_id: uuid.UUID = OWNER_ID,
) -> AigcJob:
    job = AigcJob(
        owner_id=owner_id,
        kind=aigc.KIND_TEXT2VIDEO,
        status=status,
        prompt=prompt,
        params_json=aigc.VideoParams(seed=1).to_json(),
        created_at=created_at,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_claim_takes_the_oldest_queued_job(session: Session) -> None:
    older = _job(session, created_at=BASE_TIME, prompt="older")
    newer = _job(session, created_at=BASE_TIME + timedelta(minutes=5), prompt="newer")

    claimed = aigc_runner._claim_next_job(session)

    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == aigc.STATUS_RUNNING
    assert claimed.progress == 1
    assert claimed.started_at is not None
    assert session.get(AigcJob, newer.id).status == aigc.STATUS_QUEUED


def test_claim_returns_none_when_the_queue_is_empty(session: Session) -> None:
    assert aigc_runner._claim_next_job(session) is None

    _job(session)
    assert aigc_runner._claim_next_job(session) is not None

    # 领完之后同一行不会再被领第二次。
    assert aigc_runner._claim_next_job(session) is None


def test_claim_ignores_jobs_that_are_not_queued(session: Session) -> None:
    _job(session, status=aigc.STATUS_RUNNING, created_at=BASE_TIME, prompt="running")
    _job(
        session,
        status=aigc.STATUS_SUCCEEDED,
        created_at=BASE_TIME + timedelta(minutes=1),
        prompt="done",
    )
    queued = _job(session, created_at=BASE_TIME + timedelta(minutes=2))

    claimed = aigc_runner._claim_next_job(session)

    assert claimed is not None
    assert claimed.id == queued.id


def test_claim_statement_uses_skip_locked_on_postgresql() -> None:
    sql = str(aigc_runner._claim_statement().compile(dialect=postgresql.dialect()))
    normalized = " ".join(sql.split())

    assert normalized.endswith("FOR UPDATE SKIP LOCKED")
    assert "ORDER BY aigcjob.created_at" in normalized


def test_claim_skips_a_row_locked_by_another_transaction(db: Session) -> None:
    from app.core.db import engine

    owner = create_random_user(db)
    first = _job(
        db,
        created_at=BASE_TIME,
        prompt="locked",
        owner_id=owner.id,
    )
    second = _job(
        db,
        created_at=BASE_TIME + timedelta(minutes=1),
        prompt="available",
        owner_id=owner.id,
    )
    try:
        with Session(engine) as holder:
            locked = holder.exec(
                select(AigcJob).where(AigcJob.id == first.id).with_for_update()
            ).first()
            assert locked is not None
            # 如果 SKIP LOCKED 失效，这里会等待并被 lock_timeout 打断，而不是挂住测试。
            db.exec(text("SET LOCAL lock_timeout = '2s'"))
            claimed = aigc_runner._claim_next_job(db)

        assert claimed is not None
        assert claimed.id == second.id

        db.expire_all()
        assert db.get(AigcJob, first.id).status == aigc.STATUS_QUEUED
    finally:
        # 领取失败时事务可能已中止，先回滚再清理，避免 cleanup 连带失败。
        db.rollback()
        db.exec(delete(AigcJob).where(AigcJob.id.in_([first.id, second.id])))
        db.commit()
