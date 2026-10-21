#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python3 scripts/setup-local.py
if ! docker info >/dev/null 2>&1; then
  echo "请先启动 Docker Desktop，再运行本脚本。" >&2
  exit 1
fi
docker compose -p devassist up -d --wait db
uv sync --frozen --all-groups --python 3.14
bun install --frozen-lockfile
(cd backend && uv run --frozen bash scripts/prestart.sh)

python3 - <<'PY'
import socket

for port in (8000, 5173):
    with socket.socket() as probe:
        # Match Uvicorn's socket options; Docker's wildcard socket can coexist
        # with an IPv4 listener, and a bare bind would report a false conflict.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"IPv4 端口 {port} 无法绑定，请检查占用进程：{exc}")
PY

backend_pid=""
frontend_pid=""
rag_pid=""
cleanup() {
  for child_pid in "$backend_pid" "$frontend_pid" "$rag_pid"; do
    if [[ -n "$child_pid" ]]; then
      kill "$child_pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

if [[ "${1:-}" == "--with-rag" ]]; then
  docker compose -p devassist-localdb -f compose.local-databases.yml up -d --wait mysql redis
  docker compose -p devassist-localvector --env-file .local/milvus.env -f compose.local-vector.yml up -d --wait
  (cd backend && uv run --frozen python -m app.learning_store)
  bash scripts/rag-worker.sh &
  rag_pid=$!
  export DEVASSIST_RAG_PID="$rag_pid"
  export RAG_WORKER_ENABLED=true
  export RAG_CACHE_ENABLED=true
  export RAG_VECTOR_STORE=milvus
  export RAG_WORKER_URL="http://127.0.0.1:${RAG_WORKER_PORT:-8765}"
  python3 - <<'PY'
import json
import os
import time
import urllib.request
from pathlib import Path

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(180):
    try:
        os.kill(int(os.environ['DEVASSIST_RAG_PID']), 0)
    except ProcessLookupError:
        raise SystemExit('本地向量进程已经退出，请检查上方日志。') from None
    try:
        token = Path('.local/zebao-rag/worker.token').read_text().strip()
        request = urllib.request.Request(os.environ['RAG_WORKER_URL'] + '/health', headers={'Authorization': 'Bearer ' + token})
        with opener.open(request, timeout=1) as response:
            if json.load(response).get('status') == 'ready':
                break
    except (OSError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit('本地向量服务未就绪，请检查模型缓存和上方日志。')
PY
fi

# exec keeps each PID attached to the process started by this script.
(cd backend && exec uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8000) &
backend_pid=$!
(cd frontend && exec bun run dev --host 127.0.0.1 --port 5173 --strictPort) &
frontend_pid=$!
echo "前端：http://localhost:5173/chat；后端：http://127.0.0.1:8000/docs"
echo "按 Ctrl+C 结束本脚本启动的应用进程；各数据库与数据卷保留。"

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  if [[ -n "$rag_pid" ]] && ! kill -0 "$rag_pid" 2>/dev/null; then
    echo "本地向量服务退出，请查看上方错误信息。" >&2
    exit 1
  fi
  sleep 1
done
echo "开发服务退出，请查看上方错误信息。" >&2
exit 1
