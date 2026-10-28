#!/usr/bin/env python3
"""Exercise a disposable DevAssist runtime without reading .env or logging secrets.

Run from backend with its existing Python environment. DATABASE_URL must point to
a loopback PostgreSQL database named devassist_validation_* on a nondefault port.
The API must use that same disposable database and an isolated code workspace.
No requests are retried; synthetic data remains for inspection and DB teardown.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pwdlib import PasswordHash
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.aigc_lock import exclusive_aigc_runner  # noqa: E402
from app.models import User  # noqa: E402


class VerificationFailure(Exception):
    """A fixed, nonsecret description of an unmet acceptance check."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def validated_targets(base_url: str) -> tuple[str, URL]:
    """Fail before I/O unless both configured targets are explicitly disposable."""
    api = urlsplit(base_url)
    require(
        api.scheme == "http"
        and is_loopback(api.hostname)
        and api.port is not None
        and api.username is None
        and api.password is None
        and not api.query
        and not api.fragment
        and api.path.rstrip("/") in {"", "/api/v1"},
        "API target must be a loopback HTTP URL with an explicit port",
    )
    raw_database_url = os.environ.get("DATABASE_URL", "")
    require(bool(raw_database_url), "DATABASE_URL is required")
    database = make_url(raw_database_url)
    require(
        database.drivername in {"postgresql", "postgresql+psycopg"}
        and is_loopback(database.host)
        and database.port is not None
        and database.port != 5432
        and 1 <= database.port <= 65535
        and (database.database or "").startswith("devassist_validation_")
        and not database.query,
        "Database target must be a dedicated loopback validation database",
    )
    # The application already depends on psycopg; do not fall back to psycopg2.
    database = database.set(drivername="postgresql+psycopg")
    root = base_url.rstrip("/")
    return (root if api.path.rstrip("/") else root + "/api/v1"), database


def check_lock(database: URL) -> None:
    """Use separate physical PostgreSQL sessions, never mock the advisory lock."""
    engines = [
        create_engine(
            database,
            poolclass=NullPool,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
        )
        for _ in range(2)
    ]
    try:
        with engines[0].connect() as first, engines[1].connect() as second:
            first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
            second_pid = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
            require(first_pid != second_pid, "Lock check needs independent DB sessions")
            for connection in (first, second):
                actual = connection.execute(
                    text("SELECT current_database()")
                ).scalar_one()
                require(
                    actual == database.database, "Connected to an unexpected database"
                )
        with exclusive_aigc_runner(engines[0]):
            rejected = False
            try:
                with exclusive_aigc_runner(engines[1]):
                    pass
            except RuntimeError:
                rejected = True
            require(rejected, "Second AIGC runner was not rejected")
        with exclusive_aigc_runner(engines[1]):
            pass
    finally:
        for engine in engines:
            engine.dispose()


@contextmanager
def validation_identity(database: URL) -> Iterator[tuple[str, str, str]]:
    """Provision a marker only in the validated DB, then remove it on every exit."""
    engine = create_engine(
        database,
        poolclass=NullPool,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    marker_id = uuid.uuid4()
    email = f"runtime-identity-{marker_id.hex}@example.com"
    password = secrets.token_urlsafe(24)
    inserted = False
    try:
        with Session(engine) as session:
            actual = session.execute(text("SELECT current_database()")).scalar_one()
            require(actual == database.database, "Identity marker DB did not match")
            session.add(
                User(
                    id=marker_id,
                    email=email,
                    is_active=True,
                    is_superuser=False,
                    hashed_password=PasswordHash.recommended().hash(password),
                    full_name="Runtime DB identity marker",
                )
            )
            session.commit()
            inserted = True
        yield str(marker_id), email, password
    finally:
        try:
            if inserted:
                with Session(engine) as session:
                    session.execute(delete(User).where(User.id == marker_id))
                    session.commit()
        finally:
            engine.dispose()


class RuntimeVerifier:
    def __init__(self, api: str, report: dict[str, Any]) -> None:
        self.client = httpx.Client(
            base_url=api + "/",
            timeout=httpx.Timeout(180, connect=5, write=10, pool=5),
            follow_redirects=False,
            trust_env=False,
        )
        self.report = report
        self.run_id = uuid.uuid4().hex

    def request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        token: str | None = None,
        expected: int = 200,
        expected_error_code: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self.client.request(method, path, headers=headers, **kwargs)
        self.report["checks"].append(
            {
                "name": name,
                "passed": response.status_code == expected,
                "http_status": response.status_code,
            }
        )
        require(response.status_code == expected, f"{name}: unexpected HTTP status")
        if expected_error_code is not None:
            try:
                error_body = response.json()
            except ValueError:
                raise VerificationFailure(f"{name}: expected an error JSON object") from None
            detail = error_body.get("detail") if isinstance(error_body, dict) else None
            require(
                isinstance(detail, dict) and detail.get("code") == expected_error_code,
                f"{name}: unexpected error code",
            )
            return {}
        if expected in {403, 404, 409}:
            return {}
        body = response.json()
        require(isinstance(body, dict), f"{name}: expected a JSON object")
        return body

    def account(self, suffix: str) -> str:
        email = f"runtime-{self.run_id}-{suffix}@example.com"
        password = secrets.token_urlsafe(24)
        self.request(
            f"signup_{suffix}",
            "POST",
            "users/signup",
            json={
                "email": email,
                "password": password,
                "full_name": "Runtime validation",
            },
        )
        login = self.request(
            f"login_{suffix}",
            "POST",
            "login/access-token",
            data={"username": email, "password": password},
        )
        token = login.get("access_token")
        require(isinstance(token, str) and bool(token), "Login did not return a token")
        self.report["synthetic_accounts"].append(email)
        return token

    def verify_database_identity(self, database: URL) -> None:
        """Prove the API reads the same DB before any API business writes."""
        with validation_identity(database) as (marker_id, email, password):
            login = self.request(
                "api_database_identity_login",
                "POST",
                "login/access-token",
                data={"username": email, "password": password},
            )
            token = login.get("access_token")
            require(
                isinstance(token, str) and bool(token),
                "Identity login returned no token",
            )
            identity = self.request(
                "api_database_identity_me",
                "GET",
                "users/me",
                token=token,
            )
            require(
                identity.get("id") == marker_id and identity.get("email") == email,
                "API does not use the validation database",
            )
        self.report["api_database_identity_verified"] = True

    def run(self, database: URL) -> None:
        self.verify_database_identity(database)
        first = self.account("a")
        second = self.account("b")
        fact = "ASTER-7319"
        filename = f"runtime-{self.run_id}.md"
        document = (
            "# Aster validation project\n\n"
            f"The release code for the Aster validation project is {fact}.\n"
            "This is synthetic runtime acceptance data.\n"
        )
        uploaded = self.request(
            "document_upload",
            "POST",
            "documents",
            token=first,
            expected=201,
            files={"file": (filename, document.encode(), "text/markdown")},
        )
        document_id = uploaded["document"]["id"]
        self.report["synthetic_document"] = {"filename": filename, "id": document_id}
        self.request(
            "document_owner_read",
            "GET",
            f"documents/{document_id}",
            token=first,
        )
        self.request(
            "document_other_owner_denied",
            "GET",
            f"documents/{document_id}",
            token=second,
            expected=404,
        )
        question = "What is the release code for the Aster validation project?"
        result = self.request(
            "bm25_search",
            "POST",
            "documents/search",
            token=first,
            json={"query": question, "document_id": document_id, "method": "bm25"},
        )
        require(
            any(
                s.get("document_id") == document_id and fact in s.get("content", "")
                for s in result.get("sources", [])
            ),
            "BM25 did not return the synthetic evidence",
        )
        self.request(
            "search_other_owner_denied",
            "POST",
            "documents/search",
            token=second,
            expected=404,
            json={"query": question, "document_id": document_id, "method": "bm25"},
        )
        payload = {
            "message": question,
            "use_documents": True,
            "document_id": document_id,
            "retrieval_method": "bm25",
            "client_request_id": str(uuid.uuid4()),
        }
        answer = self.request(
            "rag_model_call", "POST", "chat", token=first, json=payload
        )
        require(
            isinstance(answer.get("answer"), str)
            and bool(answer["answer"].strip())
            and any(
                s.get("document_id") == document_id for s in answer.get("sources", [])
            ),
            "RAG answer is empty or missing the expected source",
        )
        self.report["answer_quality"] = {
            "expected_fact": fact,
            "expected_fact_present": fact.casefold() in answer["answer"].casefold(),
            "answer_characters": len(answer["answer"]),
            "source_count": len(answer["sources"]),
            "scope": "One synthetic example; separate from runtime acceptance",
        }
        history = self.request(
            "history_after_answer", "GET", "chat/history", token=first
        )
        require(history.get("count") == 1, "Expected exactly one saved chat turn")
        turn_id = history["data"][0]["id"]
        detail = self.request(
            "history_owner_read",
            "GET",
            f"chat/history/{turn_id}",
            token=first,
        )
        require(
            detail.get("answer") == answer["answer"],
            "Saved answer differs from response",
        )
        require(
            detail.get("sources") == answer["sources"],
            "Saved sources differ from response",
        )
        replay = self.request("chat_replay", "POST", "chat", token=first, json=payload)
        require(
            replay.get("answer") == answer["answer"], "Replay changed the saved answer"
        )
        after = self.request("history_after_replay", "GET", "chat/history", token=first)
        require(after.get("count") == 1, "Replay created another chat turn")
        self.request(
            "chat_request_id_conflict",
            "POST",
            "chat",
            token=first,
            expected=409,
            json={**payload, "message": question + " Please explain."},
        )
        self.request(
            "history_other_owner_denied",
            "GET",
            f"chat/history/{turn_id}",
            token=second,
            expected=404,
        )
        self.verify_workspace(first, second, fact)

    def verify_workspace(self, first: str, second: str, fact: str) -> None:
        """Exercise only this run's synthetic files and version conflicts."""
        code_path = f"runtime_{self.run_id}.py"
        other_path = f"runtime_other_{self.run_id}.py"
        before = "RELEASE_CODE = 'pending'\n"
        other_before = "OTHER = 'pending'\n"
        manual = "RELEASE_CODE = 'manual'\n"
        updated = f"RELEASE_CODE = '{fact}'\n"
        other_updated = "OTHER = 'applied'\n"
        self.request(
            "workspace_upload", "POST", "code/workspace/files", token=first,
            files=[
                ("files", (code_path, before.encode(), "text/plain")),
                ("files", (other_path, other_before.encode(), "text/plain")),
            ],
        )

        def read(path: str, name: str) -> dict[str, Any]:
            return self.request(name, "GET", f"code/workspace/files/{path}", token=first)

        def version(body: dict[str, Any], name: str) -> str:
            value = body.get("base_version")
            require(isinstance(value, str) and bool(value), f"{name}: missing base_version")
            return value

        def diff(path: str, content: str, name: str) -> str:
            body = self.request(name, "POST", "code/workspace/diff", token=first,
                                json={"path": path, "content": content})
            return version(body, name)

        initial = read(code_path, "workspace_initial_read")
        require(initial.get("content") == before, "Uploaded workspace content mismatch")
        initial_version = version(initial, "workspace_initial_read")
        first_version = diff(code_path, updated, "workspace_initial_diff")
        other_version = diff(other_path, other_updated, "workspace_other_diff")
        require(initial_version == first_version, "Read and diff versions differ")

        self.request(
            "workspace_manual_save", "PUT", "code/workspace/files", token=first,
            json={"path": code_path, "content": manual, "base_version": initial_version},
        )
        self.request(
            "workspace_manual_conflict", "PUT", "code/workspace/files", token=first,
            expected=409, expected_error_code="workspace_conflict",
            json={"path": code_path, "content": updated, "base_version": initial_version},
        )
        stale_edits = [
            {"path": code_path, "content": updated, "base_version": first_version},
            {"path": other_path, "content": other_updated, "base_version": other_version},
        ]
        self.request(
            "workspace_apply_conflict", "POST", "code/workspace/apply", token=first,
            expected=409, expected_error_code="workspace_conflict",
            json={"permission": "workspace_modify", "approved": True, "edits": stale_edits},
        )
        require(read(code_path, "workspace_conflict_read").get("content") == manual,
                "Conflicting file changed unexpectedly")
        require(read(other_path, "workspace_conflict_other_read").get("content") == other_before,
                "Batch conflict modified another file")

        stale_edits[0]["base_version"] = diff(code_path, updated, "workspace_fresh_diff")
        self.request(
            "workspace_apply", "POST", "code/workspace/apply", token=first,
            json={"permission": "workspace_modify", "approved": True, "edits": stale_edits},
        )
        require(read(code_path, "workspace_readback").get("content") == updated,
                "Workspace content did not match applied edit")
        require(read(other_path, "workspace_other_readback").get("content") == other_updated,
                "Other workspace file did not match applied edit")
        self.request(
            "workspace_other_owner_denied", "GET", f"code/workspace/files/{code_path}",
            token=second, expected=404,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Disposable loopback API URL")
    parser.add_argument(
        "--report", type=Path, help="New report path; never overwrite a file"
    )
    args = parser.parse_args()
    if args.report:
        report_path = args.report.absolute()
        descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    else:
        descriptor, name = tempfile.mkstemp(prefix="devassist-runtime-", suffix=".json")
        report_path = Path(name)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "checks": [],
        "synthetic_accounts": [],
        "side_effects": "Synthetic accounts, document, chat turn and workspace file remain",
    }
    verifier = None
    try:
        api, database = validated_targets(args.base_url)
        check_lock(database)
        report["checks"].append(
            {"name": "aigc_independent_sessions_lock", "passed": True}
        )
        verifier = RuntimeVerifier(api, report)
        verifier.run(database)
        report["status"] = "passed"
    except VerificationFailure as exc:
        report["failure"] = str(exc)
    except Exception as exc:
        # Exception messages and response bodies can contain DSNs or credentials.
        report["failure"] = "Runtime validation raised " + type(exc).__name__
    finally:
        if verifier:
            verifier.client.close()
        report["finished_at"] = datetime.now(UTC).isoformat()
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    sys.stdout.write(
        json.dumps(
            {
                "report": str(report_path),
                "status": report["status"],
                "checks_passed": sum(item["passed"] for item in report["checks"]),
            }
        )
        + "\n"
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
