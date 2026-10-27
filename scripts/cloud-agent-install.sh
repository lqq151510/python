#!/usr/bin/env bash
# Cloud Agent 构建：占位 .env、Python 与前端依赖。不下载嵌入模型，不启动数据库。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if ! command -v uv >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$tmp" sh
  sudo install -m 755 "$tmp/uv" /usr/local/bin/uv
  if [[ -x "$tmp/uvx" ]]; then
    sudo install -m 755 "$tmp/uvx" /usr/local/bin/uvx
  fi
  rm -rf "$tmp"
fi

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash -s "bun-v1.3.12"
  sudo ln -sfn "${HOME}/.bun/bin/bun" /usr/local/bin/bun
fi

bash scripts/ci-env.sh > .env
printf '%s\n' 'VITE_API_URL=http://127.0.0.1:8000' > frontend/.env
uv sync --frozen --all-groups --python 3.14
bun install --frozen-lockfile

# shellcheck source=scripts/cloud-agent-docker.sh
source "$root/scripts/cloud-agent-docker.sh"
cloud_agent_install_docker
if cloud_agent_start_docker; then
  echo "cloud-agent: caching postgres image"
  if ! cloud_agent_compose -p devassist pull db; then
    echo "cloud-agent: postgres pull failed; start will retry"
  fi
else
  echo "cloud-agent: docker unavailable during install; start will pull postgres"
  cloud_agent_docker_log_tail
fi
if sudo docker info >/dev/null 2>&1 || docker info >/dev/null 2>&1; then
  if cloud_agent_stop_docker; then
    echo "cloud-agent: docker daemon stopped for snapshot"
  else
    echo "cloud-agent: docker daemon was still running at snapshot"
  fi
fi
