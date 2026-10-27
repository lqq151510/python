#!/usr/bin/env bash
# Cloud Agent 启动：拉起 Postgres，并迁移数据库。不下载嵌入模型。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

sudo service docker start
for _ in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker info >/dev/null

docker compose -p devassist up -d --wait db
(cd backend && uv run --frozen bash scripts/prestart.sh)
