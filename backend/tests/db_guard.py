"""Fail closed before the test fixture touches a PostgreSQL database."""

import re

from sqlalchemy.engine import URL


def require_disposable_database(url: URL, marker: str | None) -> None:
    """Require an explicitly named, locally created disposable database."""
    valid_name = re.fullmatch(r"devassist_(?:test|validation)_[0-9a-f]{12}", marker or "")
    if (
        not valid_name
        or url.database != marker
        or url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or bool(url.query)
    ):
        raise RuntimeError(
            "Refusing database tests: use scripts/verify-isolated.py or "
            "backend/scripts/test-local.py to create an isolated test database."
        )
