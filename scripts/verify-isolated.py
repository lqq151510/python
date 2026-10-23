"""Validate against disposable PostgreSQL and loopback Ollama, without app .env.

Run with the repository's Python: .venv/bin/python scripts/verify-isolated.py
Requires an already running Docker Engine and cached postgres:16-alpine image.
Only the container created by this invocation is stopped; no existing volumes
or application databases are opened, migrated, or removed.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:16-alpine"


def cleanup_container(name: str, run_id: str) -> bool:
    """Clean only our labelled container, even if docker run lost its response."""

    def call(arguments: list[str], timeout: int = 10):
        return subprocess.run(
            ["docker", *arguments],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def absent(identifier: str) -> bool:
        result = call(["container", "inspect", identifier])
        if result.returncode == 0:
            return False
        # Distinguish a missing container from a disconnected Docker daemon.
        return call(["info", "--format", "{{.ServerVersion}}"]).returncode == 0

    try:
        inspected = call(
            [
                "inspect",
                "--format",
                '{{.Id}} {{index .Config.Labels "devassist.validation"}}',
                name,
            ]
        )
        if inspected.returncode:
            return absent(name)
        identity = inspected.stdout.strip().split()
        if len(identity) != 2 or identity[1] != run_id:
            return False
        container_id = identity[0]
        try:
            call(["stop", "--time", "5", container_id], timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        if absent(container_id):
            return True
        # The ID cannot be reused by an unrelated container with the same name.
        call(["rm", "--force", container_id], timeout=15)
        return absent(container_id)
    except (OSError, subprocess.SubprocessError):
        return False


def stop_api(process: subprocess.Popen) -> bool:
    """Keep later cleanup running if process termination itself fails."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        return process.poll() is not None
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5-1.5b-instruct:latest")
    parser.add_argument(
        "--skip-runtime", action="store_true", help="Only migrate and run API tests."
    )
    args = parser.parse_args()
    run_id = secrets.token_hex(6)
    container = f"devassist-validation-{run_id}"
    database = f"devassist_validation_{run_id}"
    report_dir = ROOT / ".local" / "isolated-validation" / run_id
    report_dir.mkdir(parents=True, mode=0o700)
    password = secrets.token_hex(24)
    summary = {
        "run_id": run_id,
        "container": container,
        "model": args.model,
        "database_is_disposable": True,
        "checks": {},
        "cleanup": {},
    }
    api: subprocess.Popen | None = None
    api_log = None
    creation_attempted = False
    temporary_context = None
    return_code = 1

    def run(command: list[str], *, env=None, cwd=None, log=None, timeout=120):
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = result.stdout.replace(password, "[redacted]")
        if log:
            path = report_dir / log
            path.write_text(output)
            path.chmod(0o600)
        if result.returncode:
            raise RuntimeError(
                f"{command[0]} failed (exit {result.returncode}); see {log or 'preflight'}"
            )
        return output.strip()

    try:
        run(["docker", "info", "--format", "{{.ServerVersion}}"])
        run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
        if not args.skip_runtime:
            opener = build_opener(ProxyHandler({}))
            with opener.open("http://127.0.0.1:11434/api/tags", timeout=5) as response:
                models = json.load(response)["models"]
            if args.model not in {item["name"] for item in models}:
                raise RuntimeError(
                    "Requested Ollama model is not installed; no model was downloaded."
                )
        temporary_context = tempfile.TemporaryDirectory(prefix="devassist-validation-")
        temporary = temporary_context.name
        temporary_path = Path(temporary)
        cwd = temporary_path / "run"
        cwd.mkdir()
        envfile = temporary_path / "postgres.env"
        envfile.write_text(f"POSTGRES_PASSWORD={password}\nPOSTGRES_DB={database}\n")
        envfile.chmod(0o600)
        creation_attempted = True
        run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--pull=never",
                "--name",
                container,
                "--label",
                f"devassist.validation={run_id}",
                "--env-file",
                str(envfile),
                "--publish",
                "127.0.0.1::5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
                IMAGE,
            ]
        )
        port = int(run(["docker", "port", container, "5432/tcp"]).rsplit(":", 1)[1])
        deadline = time.monotonic() + 45
        while True:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "postgres",
                    "-d",
                    database,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Disposable PostgreSQL did not become ready.")
            time.sleep(0.5)
        environment = dict(os.environ)
        for key in list(environment):
            if key.lower() in {
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            }:
                del environment[key]
        environment.update(
            {
                "PATH": str(ROOT / ".venv" / "bin")
                + os.pathsep
                + environment.get("PATH", ""),
                "PYTHONPATH": str(ROOT / "backend"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "DATABASE_URL": f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/{database}",
                "PROJECT_NAME": "DevAssist validation",
                "SECRET_KEY": secrets.token_hex(32),
                "FIRST_SUPERUSER": "admin@validation.example.com",
                "FIRST_SUPERUSER_PASSWORD": secrets.token_hex(20),
                "FASTAPI_ENV": "development",
                "SENTRY_DSN": "",
                "SMTP_HOST": "",
                "EMAILS_FROM_EMAIL": "",
                "VERIFY_MULTIDB": "0",
                "VERIFY_REAL_RAG": "0",
                "AIGC_ENABLED": "false",
                "RAG_WORKER_ENABLED": "false",
                "RAG_CACHE_ENABLED": "false",
                "MYSQL_DATABASE_URL": "",
                "CODE_WORKSPACE_DIR": str(temporary_path / "workspaces"),
                "IMAGE_LIBRARY_DIR": str(temporary_path / "images"),
                "AIGC_OUTPUT_DIR": str(temporary_path / "aigc"),
                "DEEPSEEK_API_KEY": "local-validation",
                "DEEPSEEK_BASE_URL": "http://127.0.0.1:11434/v1",
                "DEEPSEEK_MODEL": args.model,
                "CHAT_MAX_TOKENS": "1024",
                "CODE_API_KEY": "",
                "CODE_BASE_URL": "",
                "CODE_MODEL": "",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            }
        )
        python = str(ROOT / ".venv" / "bin" / "python")
        migrate = (
            "import sys; from alembic.config import Config; from alembic import command; "
            "c=Config(sys.argv[1]); c.set_main_option('script_location',sys.argv[2]); command.upgrade(c,'head')"
        )
        run(
            [
                python,
                "-c",
                migrate,
                str(ROOT / "backend/alembic.ini"),
                str(ROOT / "backend/app/alembic"),
            ],
            env=environment,
            cwd=cwd,
            log="migrations.log",
        )
        summary["checks"]["migrations"] = "passed"
        print("Disposable PostgreSQL ready; migrations passed.", flush=True)
        test_output = run(
            [
                python,
                "-m",
                "pytest",
                str(ROOT / "backend/tests"),
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            env=environment,
            cwd=cwd,
            log="pytest.log",
            timeout=300,
        )
        summary["checks"]["backend_tests"] = test_output.splitlines()[-1]
        print(summary["checks"]["backend_tests"], flush=True)
        if not args.skip_runtime:
            environment["CHAT_MAX_TOKENS"] = "256"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                api_port = probe.getsockname()[1]
            api_log = (report_dir / "api.log").open("w")
            (report_dir / "api.log").chmod(0o600)
            api = subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=cwd,
                env=environment,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            base_url = f"http://127.0.0.1:{api_port}"
            deadline = time.monotonic() + 30
            while True:
                if api.poll() is not None:
                    raise RuntimeError("Temporary API exited; see api.log.")
                try:
                    with opener.open(
                        base_url + "/api/v1/utils/health-check/", timeout=2
                    ) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Temporary API did not become ready.")
                time.sleep(0.25)
            run(
                [
                    python,
                    str(ROOT / "backend/scripts/verify-runtime.py"),
                    "--base-url",
                    base_url,
                    "--report",
                    str(report_dir / "runtime.json"),
                ],
                env=environment,
                cwd=cwd,
                log="runtime.log",
                timeout=150,
            )
            summary["checks"]["runtime"] = "passed"
            print("Real PostgreSQL lock and HTTP/model checks passed.", flush=True)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        summary["error"] = str(exc).replace(password, "[redacted]")
        print(summary["error"], file=sys.stderr)
        return_code = 1
    else:
        return_code = 0
    finally:
        if api is not None:
            summary["cleanup"]["api_stopped"] = stop_api(api)
            if not summary["cleanup"]["api_stopped"]:
                return_code = 1
        if api_log is not None:
            try:
                api_log.close()
            except OSError:
                summary["cleanup"]["api_log_closed"] = False
                return_code = 1
        if creation_attempted:
            summary["cleanup"]["container_stopped"] = cleanup_container(
                container, run_id
            )
            if not summary["cleanup"]["container_stopped"]:
                return_code = 1
        if temporary_context is not None:
            try:
                temporary_context.cleanup()
                summary["cleanup"]["temporary_directory_removed"] = True
            except OSError:
                summary["cleanup"]["temporary_directory_removed"] = False
                return_code = 1
        path = report_dir / "summary.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        print(f"Validation summary: {path}", flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
