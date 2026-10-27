#!/usr/bin/env bash
# Cloud Agent 启动：拉起 Postgres，并迁移数据库。不下载嵌入模型。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

bash scripts/ci-env.sh > .env
printf '%s\n' 'VITE_API_URL=http://127.0.0.1:8000' > frontend/.env

# shellcheck source=scripts/cloud-agent-docker.sh
source "$root/scripts/cloud-agent-docker.sh"
if ! cloud_agent_start_docker; then
  echo "cloud-agent: docker did not become ready" >&2
  cloud_agent_docker_log_tail >&2
  exit 1
fi

cloud_agent_compose -p devassist pull db
cloud_agent_compose -p devassist up -d --wait db
(cd backend && uv run --frozen bash scripts/prestart.sh)
