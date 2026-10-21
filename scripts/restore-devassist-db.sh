#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$ROOT_DIR/.local/devassist-backups"

if [[ $# -ne 1 ]]; then
  echo "用法：DEVASSIST_RESTORE_CONFIRM=RESTORE_DEVASSIST_DATABASE $0 <备份文件>" >&2
  exit 64
fi
if [[ "${DEVASSIST_RESTORE_CONFIRM:-}" != "RESTORE_DEVASSIST_DATABASE" ]]; then
  echo "恢复会替换 DevAssist 的 app 数据库；请先确认备份并设置 DEVASSIST_RESTORE_CONFIRM。" >&2
  exit 64
fi

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
RESOLVED_BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
case "$BACKUP_FILE" in
  "$RESOLVED_BACKUP_DIR"/*) ;;
  *) echo "只允许从 .local/devassist-backups 恢复。" >&2; exit 64 ;;
esac
if [[ ! -f "$BACKUP_FILE" || -L "$BACKUP_FILE" || ! -s "$BACKUP_FILE" ]]; then
  echo "备份文件不存在、为空或是符号链接。" >&2
  exit 66
fi

cd "$ROOT_DIR"
docker compose --project-name devassist exec -T db \
  pg_restore --list - < "$BACKUP_FILE" >/dev/null
echo "先为当前数据库创建恢复前备份。"
"$ROOT_DIR/scripts/backup-devassist-db.sh"

docker compose --project-name devassist stop backend
if docker compose --project-name devassist exec -T db \
  dropdb --force --username=postgres app &&
  docker compose --project-name devassist exec -T db \
    createdb --username=postgres app &&
  docker compose --project-name devassist exec -T db \
    pg_restore --exit-on-error --no-owner --username=postgres --dbname=app < "$BACKUP_FILE"; then
  docker compose --project-name devassist start backend
  echo "数据库已恢复；请检查 Alembic 版本、健康接口和应用日志。"
else
  echo "恢复失败；backend 保持停止状态。请根据恢复前备份修复数据库后再启动服务。" >&2
  exit 1
fi
