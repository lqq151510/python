"""End-to-end smoke test for the real chat chain: login, upload docs, ask questions.

It talks to a running devassist backend over HTTP, so it exercises exactly what the
browser does: auth, document upload, retrieval, the model call and chat persistence.

Usage (backend must be running and pointed at a real model provider):

    export SMOKE_PASSWORD='换成你的本地测试密码'
    cd backend
    ../.venv/bin/python scripts/smoke-llm-chat.py \
        --email llm-smoke@example.com --password "$SMOKE_PASSWORD" \
        --doc ../docs/VERIFICATION.md \
        --question "后端测试怎么跑？需要哪些前置条件？" \
        --out ../.local/llm-chat/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


def _client(base_url: str) -> httpx.Client:
    # trust_env=False keeps the local worker traffic off the system proxy.
    return httpx.Client(
        base_url=base_url, timeout=httpx.Timeout(120.0), trust_env=False
    )


def _login(client: httpx.Client, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": password},
    )
    if response.status_code != 200:
        raise SystemExit(f"登录失败 {response.status_code}: {response.text}")
    return response.json()["access_token"]


def _upload(client: httpx.Client, path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/documents",
            files={"file": (path.name, handle, "text/markdown")},
        )
    if response.status_code not in (200, 201):
        raise SystemExit(f"上传失败 {response.status_code}: {response.text}")
    payload = response.json()
    document = payload["document"]
    print(
        f"  上传 {document['filename']}: id={document['id']} "
        f"chunks={document['chunk_count']} duplicate={payload['duplicate']}"
    )
    return payload


def _ask(
    client: httpx.Client,
    question: str,
    *,
    use_documents: bool,
    method: str | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "message": question,
        "use_documents": use_documents,
        "client_request_id": str(uuid.uuid4()),
    }
    if method:
        body["retrieval_method"] = method
    started = time.perf_counter()
    response = client.post("/api/v1/chat", json=body)
    elapsed = round(time.perf_counter() - started, 2)
    record: dict[str, Any] = {
        "question": question,
        "use_documents": use_documents,
        "retrieval_method": method,
        "status": response.status_code,
        "elapsed_seconds": elapsed,
    }
    if response.status_code == 200:
        payload = response.json()
        record["answer"] = payload["answer"]
        record["retrieval_method"] = payload.get("retrieval_method")
        record["retrieval_warning"] = payload.get("retrieval_warning")
        record["request_id"] = payload.get("request_id")
        record["sources"] = [
            {
                "label": source.get("citation"),
                "filename": source.get("filename"),
                "heading": source.get("heading"),
                "line_start": source.get("line_start"),
                "line_end": source.get("line_end"),
            }
            for source in payload.get("sources", [])
        ]
    else:
        record["error"] = response.json()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--email", default="llm-smoke@example.com")
    parser.add_argument(
        "--password",
        default=os.environ.get("SMOKE_PASSWORD", ""),
        help="测试账号密码，也可设置环境变量 SMOKE_PASSWORD",
    )
    parser.add_argument("--doc", action="append", default=[], help="文档路径，可重复")
    parser.add_argument(
        "--question", action="append", default=[], help="带文档的问题，可重复"
    )
    parser.add_argument(
        "--plain-question", default="用一句话解释 FastAPI 和 Pydantic 的关系。"
    )
    parser.add_argument(
        "--methods", default="bm25", help="逗号分隔的检索方式，如 bm25,vector,hybrid"
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.password:
        parser.error("请通过 --password 或环境变量 SMOKE_PASSWORD 提供测试密码")

    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    questions = args.question or ["这个项目如何运行后端测试？需要什么前置条件？"]
    report: dict[str, Any] = {
        "base_url": args.base_url,
        "email": args.email,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "documents": [],
        "results": [],
    }

    with _client(args.base_url) as client:
        token = _login(client, args.email, args.password)
        client.headers["Authorization"] = f"Bearer {token}"
        me = client.get("/api/v1/users/me")
        print(f"登录成功: {me.json().get('email')} ({me.status_code})")

        for doc in args.doc:
            report["documents"].append(_upload(client, Path(doc)))

        print("— 无文档问答 —")
        plain = _ask(client, args.plain_question, use_documents=False, method=None)
        report["results"].append(plain)
        _print_record(plain)

        for method in methods:
            for question in questions:
                print(f"— 文档问答 method={method} —")
                record = _ask(client, question, use_documents=True, method=method)
                report["results"].append(record)
                _print_record(record)

        history = client.get("/api/v1/chat/history", params={"limit": 5})
        report["history"] = {
            "status": history.status_code,
            "count": history.json().get("count")
            if history.status_code == 200
            else None,
        }
        print(
            f"— 聊天历史 — status={history.status_code} count={report['history']['count']}"
        )

    ok = sum(1 for item in report["results"] if item["status"] == 200)
    report["summary"] = {"total": len(report["results"]), "ok": ok}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已写入 {out}")
    print(f"合计 {report['summary']['total']} 次请求，成功 {ok} 次")
    return 0 if ok == len(report["results"]) else 1


def _print_record(record: dict[str, Any]) -> None:
    print(f"  status={record['status']} 用时={record['elapsed_seconds']}s")
    if record["status"] != 200:
        print(f"  error={json.dumps(record['error'], ensure_ascii=False)}")
        return
    answer = record["answer"].strip().replace("\n", " ")
    print(f"  answer={answer[:300]}")
    for source in record["sources"]:
        print(
            f"  source={source['label']} {source['filename']}:{source['line_start']}-{source['line_end']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
