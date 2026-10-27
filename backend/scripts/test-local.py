"""Run regression tests in a newly owned database, preserving the app database."""

import os
import subprocess
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import settings


def main() -> int:
    base = make_url(str(settings.DATABASE_URL)).set(drivername="postgresql")
    if base.host not in ("localhost", "127.0.0.1"):
        raise SystemExit("测试脚本仅允许使用本地 PostgreSQL。")
    name = f"devassist_test_{uuid.uuid4().hex[:12]}"
    created = False
    with psycopg.connect(base.render_as_string(hide_password=False), autocommit=True) as connection:
        try:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
            environment = dict(os.environ)
            environment["DATABASE_URL"] = base.set(database=name).render_as_string(hide_password=False)
            root = Path(__file__).resolve().parents[1]
            print(f"使用独立临时测试数据库：{name}", flush=True)
            migrate = subprocess.run(["alembic", "upgrade", "head"], cwd=root, env=environment, check=False)
            if migrate.returncode:
                return migrate.returncode
            result = subprocess.run(["pytest", "tests/", "-q"], cwd=root, env=environment, check=False)
            return result.returncode
        finally:
            if created:
                connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                print("本次临时测试数据库已移除；应用数据库保留。", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
