#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$ROOT_DIR/.local/devassist-backups"
cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TEMP_FILE="$(mktemp "$BACKUP_DIR/.devassist-postgres-$STAMP.XXXXXXXX")"
TEMP_NAME="${TEMP_FILE##*/}"
BACKUP_FILE="$BACKUP_DIR/${TEMP_NAME#.}.dump"
trap 'rm -f "$TEMP_FILE"' EXIT

docker compose --project-name devassist exec -T db \
  pg_dump --format=custom --username=postgres --dbname=app > "$TEMP_FILE"
docker compose --project-name devassist exec -T db \
  pg_restore --list < "$TEMP_FILE" >/dev/null
docker compose --project-name devassist exec -T db \
  pg_restore --exit-on-error --file=/dev/null < "$TEMP_FILE"
# A hard link publishes atomically and never replaces an existing path.
python3 - "$TEMP_FILE" "$BACKUP_FILE" <<'PY'
import os
import sys

try:
    os.link(sys.argv[1], sys.argv[2])
except OSError as exc:
    raise SystemExit(f"备份发布失败（{type(exc).__name__}），未覆盖已有文件。") from None
PY
rm -f "$TEMP_FILE"
trap - EXIT

printf '数据库备份已创建并通过归档目录及完整解析检查：%s\n' "$BACKUP_FILE"
