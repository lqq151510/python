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
BACKUP_FILE="$(cd -P "$(dirname "$1")" && pwd -P)/$(basename "$1")"
RESOLVED_BACKUP_DIR="$(cd -P "$BACKUP_DIR" && pwd -P)"
case "$BACKUP_FILE" in
  "$RESOLVED_BACKUP_DIR"/*) ;;
  *) echo "只允许从 .local/devassist-backups 恢复。" >&2; exit 64 ;;
esac
if [[ ! -f "$BACKUP_FILE" || -L "$BACKUP_FILE" || ! -s "$BACKUP_FILE" ]]; then
  echo "备份文件不存在、为空或是符号链接。" >&2
  exit 66
fi

cd "$ROOT_DIR"
# Freeze the selected source before validation; later replacement of the
# original path must not change what is ultimately restored.
umask 077
SNAPSHOT_DIR="$(mktemp -d "$RESOLVED_BACKUP_DIR/.devassist-restore.XXXXXXXX")"
SNAPSHOT_FILE="$SNAPSHOT_DIR/source.dump"
cleanup_snapshot() {
  rm -f "$SNAPSHOT_FILE"
  rmdir "$SNAPSHOT_DIR" 2>/dev/null || true
}
trap cleanup_snapshot EXIT

python3 - "$BACKUP_FILE" "$SNAPSHOT_FILE" <<'PY'
import os
import shutil
import stat
import sys

if not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit("当前系统不支持安全打开恢复源。")
try:
    source_fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(source_fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            raise ValueError("恢复源必须是非空普通文件。")
        target_fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(target_fd, "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
except (OSError, ValueError) as exc:
    raise SystemExit(f"无法安全创建恢复快照（{type(exc).__name__}）。") from None
PY

docker compose --project-name devassist exec -T db \
  pg_restore --list < "$SNAPSHOT_FILE" >/dev/null
docker compose --project-name devassist exec -T db \
  pg_restore --exit-on-error --file=/dev/null < "$SNAPSHOT_FILE"
echo "先为当前数据库创建恢复前备份。"
"$ROOT_DIR/scripts/backup-devassist-db.sh"

docker compose --project-name devassist stop backend
if docker compose --project-name devassist exec -T db \
  dropdb --force --username=postgres app &&
  docker compose --project-name devassist exec -T db \
    createdb --username=postgres app &&
  docker compose --project-name devassist exec -T db \
    pg_restore --exit-on-error --no-owner --username=postgres --dbname=app < "$SNAPSHOT_FILE"; then
  docker compose --project-name devassist start backend
  echo "数据库已恢复；请检查 Alembic 版本、健康接口和应用日志。"
else
  echo "恢复失败；backend 保持停止状态。请根据恢复前备份修复数据库后再启动服务。" >&2
  exit 1
fi
