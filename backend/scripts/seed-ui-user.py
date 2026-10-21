"""Create or update a local UI account so browser tests can log in.

The superuser in .env is not always seeded in a fresh database, and the
Playwright specs need a deterministic account that is safe to recreate.
"""

from __future__ import annotations

import os
import sys

from sqlmodel import Session, select

from app.core.db import engine
from app.core.security import get_password_hash
from app import crud
from app.models import User, UserCreate

EMAIL = os.environ.get("SEED_EMAIL", "aigc-ui@example.com")
PASSWORD = os.environ.get("SEED_PASSWORD", "ZebaoAigc123!")
FULL_NAME = os.environ.get("SEED_FULL_NAME", "AIGC UI")


def main() -> int:
    if len(PASSWORD) < 8:
        print("SEED_PASSWORD 至少 8 位", file=sys.stderr)
        return 2
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.email == EMAIL)).first()
        if existing is None:
            user = crud.create_user(
                session=session,
                user_create=UserCreate(
                    email=EMAIL,
                    password=PASSWORD,
                    full_name=FULL_NAME,
                    is_superuser=False,
                    is_active=True,
                ),
            )
            print(f"created {user.email} id={user.id}")
        else:
            session.add(
                existing.model_copy(update={"hashed_password": get_password_hash(PASSWORD)}),
            )
            session.commit()
            print(f"updated {existing.email} id={existing.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
