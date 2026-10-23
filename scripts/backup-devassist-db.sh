#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$ROOT_DIR/.local/devassist-backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="$BACKUP_DIR/devassist-postgres-$STAMP.dump"
TEMP_FILE="$BACKUP_FILE.tmp"

cd "$ROOT_DIR"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
trap 'rm -f "$TEMP_FILE"' EXIT

docker compose --project-name devassist exec -T db \
  pg_dump --format=custom --username=postgres --dbname=app > "$TEMP_FILE"
docker compose --project-name devassist exec -T db \
  pg_restore --list - < "$TEMP_FILE" >/dev/null
mv "$TEMP_FILE" "$BACKUP_FILE"
trap - EXIT

printf '数据库备份已创建并通过归档目录检查：%s\n' "$BACKUP_FILE"
