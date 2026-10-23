"""MySQL learning journal, independent of PostgreSQL's application tables."""

import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import JSON, DateTime, String, Text, create_engine, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class LearningRecord(Base):
    __tablename__ = "devassist_learning_records"
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="note")
    title: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


@lru_cache(maxsize=1)
def engine() -> Engine:
    if settings.MYSQL_DATABASE_URL:
        url: URL | str = settings.MYSQL_DATABASE_URL.get_secret_value()
    else:
        path = Path(__file__).resolve().parents[2] / ".local/mysql.env"
        values = dotenv_values(path)
        if not all(
            values.get(key)
            for key in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")
        ):
            raise RuntimeError("MySQL configuration is missing")
        url = URL.create(
            "mysql+pymysql",
            username=values["MYSQL_USER"],
            password=values["MYSQL_PASSWORD"],
            host="127.0.0.1",
            port=3306,
            database=values["MYSQL_DATABASE"],
            query={"charset": "utf8mb4"},
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 2, "read_timeout": 3, "write_timeout": 3},
    )


def initialize() -> None:
    # Only this Base's learning table; never creates auth/document tables in MySQL.
    Base.metadata.create_all(engine())


def public(record: LearningRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "title": record.title,
        "notes": record.notes,
        "result": record.result,
        "created_at": record.created_at.isoformat() + "Z",
    }


def add(
    owner_id: uuid.UUID,
    title: str,
    notes: str = "",
    *,
    kind: str = "note",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with Session(engine()) as session:
        record = LearningRecord(
            owner_id=str(owner_id),
            title=title,
            notes=notes,
            kind=kind,
            result=result or {},
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return public(record)


def listing(owner_id: uuid.UUID) -> list[dict[str, Any]]:
    with Session(engine()) as session:
        records = session.scalars(
            select(LearningRecord)
            .where(LearningRecord.owner_id == str(owner_id))
            .order_by(LearningRecord.created_at.desc())
            .limit(20)
        ).all()
        return [public(record) for record in records]


def remove(owner_id: uuid.UUID, record_id: uuid.UUID) -> bool:
    with Session(engine()) as session:
        record = session.scalar(
            select(LearningRecord).where(
                LearningRecord.id == str(record_id),
                LearningRecord.owner_id == str(owner_id),
            )
        )
        if record is None:
            return False
        session.delete(record)
        session.commit()
        return True


if __name__ == "__main__":
    initialize()
