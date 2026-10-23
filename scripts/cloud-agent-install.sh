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
uv sync --frozen --all-groups --python 3.14
bun install --frozen-lockfile
