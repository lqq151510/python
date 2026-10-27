#!/usr/bin/env bash
# 按需启停本地数据库栈。
#
# 背景：本机是 32 GiB 统一内存的 Apple Silicon，Milvus 那一套（etcd + minio + milvus
# + attu）常驻就要数 GiB，和视频生成、嵌入微调抢同一块内存。默认把它们停掉，
# 只在需要检索或学习库时手动拉起。
#
# 用法：
#   scripts/dev-stack.sh status            看各栈状态与本机内存体检
#   scripts/dev-stack.sh mode dev          只留 PostgreSQL（默认开发）
#   scripts/dev-stack.sh mode rag          额外启动 MySQL/Redis 与 Milvus
#   scripts/dev-stack.sh mode video        只留 PostgreSQL，并体检视频生成预算
#   scripts/dev-stack.sh mode train        只留 PostgreSQL（关掉 Milvus），体检微调预算
#   scripts/dev-stack.sh start sql|vector  单独启动
#   scripts/dev-stack.sh stop  [all]       停掉 sql + vector；加 all 连 PostgreSQL 一起停
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

sql_project="devassist-localdb"
vector_project="devassist-localvector"
main_project="devassist"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

require_docker() {
  if ! docker info >/dev/null 2>&1; then
    echo "Docker 未运行，请先启动 Docker Desktop。" >&2
    return 1
  fi
}

require_milvus_env() {
  if [[ ! -f .local/milvus.env ]]; then
    echo ".local/milvus.env 不存在，先运行：python3 scripts/setup-local.py" >&2
    exit 1
  fi
}

check_memory() {
  local workload="$1"
  (
    cd "$project_dir/backend"
    python3 - "$workload" <<'PY'
import sys

from app.core import device

workload = sys.argv[1]
try:
    snapshot = device.require_headroom(workload)
except device.DeviceMemoryError as exc:
    raise SystemExit(f"[内存体检未通过] {exc}")
print(f"[内存体检通过] {device.describe(snapshot)}")
PY
  )
}

up_sql() {
  require_docker
  docker compose -p "$sql_project" -f compose.local-databases.yml up -d --wait mysql redis
}

up_vector() {
  require_docker
  require_milvus_env
  docker compose -p "$vector_project" --env-file .local/milvus.env \
    -f compose.local-vector.yml up -d --wait
}

up_postgres() {
  require_docker
  docker compose -p "$main_project" up -d --wait db
}

down_sql() {
  require_docker
  docker compose -p "$sql_project" -f compose.local-databases.yml down
}

down_vector() {
  require_docker
  docker compose -p "$vector_project" --env-file .local/milvus.env \
    -f compose.local-vector.yml down
}

print_stack() {
  local project="$1" label="$2"
  local lines
  lines="$(docker ps --filter "label=com.docker.compose.project=$project" \
    --format '  {{.Names}}  {{.Status}}' 2>/dev/null || true)"
  if [[ -z "$lines" ]]; then
    echo "${label}：未运行"
  else
    echo "${label}："
    echo "$lines"
  fi
}

status() {
  echo "== 栈状态 =="
  if docker info >/dev/null 2>&1; then
    print_stack "$main_project" "PostgreSQL（${main_project}）"
    print_stack "$sql_project" "MySQL + Redis（${sql_project}）"
    print_stack "$vector_project" "Milvus 四件套（${vector_project}）"
  else
    echo "Docker 未运行，全部栈视为未启动。"
  fi
  echo
  echo "== 内存体检 =="
  ( cd "$project_dir/backend" && python3 -m app.core.device ) || true
}

case "${1:-}" in
  status)
    status
    ;;
  start)
    case "${2:-}" in
      sql) up_sql ;;
      vector) check_memory rag_stack && up_vector ;;
      postgres) up_postgres ;;
      *) usage; exit 2 ;;
    esac
    status
    ;;
  stop)
    if [[ "${2:-}" == "all" ]]; then
      up_postgres >/dev/null 2>&1 || true
      require_docker
      docker compose -p "$main_project" down
    else
      down_sql
      down_vector
    fi
    status
    ;;
  mode)
    case "${2:-}" in
      dev)
        up_postgres
        down_sql
        down_vector
        ;;
      rag)
        up_postgres
        up_sql
        check_memory rag_stack
        up_vector
        ;;
      video)
        up_postgres
        down_sql
        down_vector
        check_memory video_generation
        ;;
      train)
        up_postgres
        down_sql
        down_vector
        check_memory embedding_finetune
        ;;
      *)
        usage
        exit 2
        ;;
    esac
    status
    ;;
  *)
    usage
    exit 2
    ;;
esac
