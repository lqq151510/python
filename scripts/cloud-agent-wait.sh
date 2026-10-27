#!/usr/bin/env bash
# 等启动脚本写完就绪标记，再开后端或前端。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ready="$root/.local/cloud-agent-ready"
for _ in $(seq 1 120); do
  if [[ -f "$ready" ]]; then
    exit 0
  fi
  sleep 1
done
echo "cloud-agent: startup did not finish" >&2
exit 1
