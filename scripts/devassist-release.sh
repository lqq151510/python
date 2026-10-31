#!/usr/bin/env bash
# Local production-mode delivery. Does not source, print or overwrite env files.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/devassist-release.sh --env-file PATH [--project-name NAME] ACTION

Actions: check, build, up, status, stop
Default project: devassist-release (custom names must start with devassist-release-)
The env file must be provided explicitly; RELEASE_PORT defaults to 8088 (loopback).
up builds the image, waits for PostgreSQL, stops the old backend, runs migrations
and initial-account setup, then starts the backend and waits for health.
stop preserves containers and all data volumes. No command deletes data.
EOF
}

fail() { printf '%s\n' "$1" >&2; exit 2; }

release_env_file=""
release_project="devassist-release"
release_action=""
while (($#)); do
  case "$1" in
    --env-file|--project-name)
      (($# >= 2)) || fail "Missing value for $1"
      if [[ "$1" == --env-file ]]; then release_env_file="$2"; else release_project="$2"; fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    check|build|up|status|stop)
      [[ -z "$release_action" ]] || fail "Provide exactly one action"
      release_action="$1"
      shift
      ;;
    *) fail "Unknown argument; use --help" ;;
  esac
done

[[ -n "$release_action" ]] || fail "Missing action; use --help"
[[ -n "$release_env_file" ]] || fail "An explicit --env-file is required"
[[ -f "$release_env_file" && -r "$release_env_file" ]] || fail "Release env file is not a readable regular file"
[[ "$release_project" =~ ^devassist-release(-[a-z0-9][a-z0-9_-]*)?$ ]] || fail "Project name must be devassist-release or devassist-release-<suffix>"

# Resolve the caller's env path before moving to the repository root.
release_env_file="$(cd -- "$(dirname -- "$release_env_file")" && pwd -P)/$(basename -- "$release_env_file")"
release_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$release_root"
command -v docker >/dev/null || fail "Docker with Compose is required"

# Compose normally gives exported shell variables precedence over --env-file.
# Keep this entry point deterministic: application settings come only from the
# explicit file. Docker connection settings, PATH and proxy settings stay intact.
release_config_vars=(
  COMPOSE_PROJECT_NAME RELEASE_TAG RELEASE_PORT PROJECT_NAME SECRET_KEY
  FIRST_SUPERUSER FIRST_SUPERUSER_PASSWORD POSTGRES_PASSWORD
  DEEPSEEK_API_KEY DEEPSEEK_BASE_URL DEEPSEEK_MODEL
  CHAT_TOTAL_TIMEOUT_SECONDS CHAT_CONNECT_TIMEOUT_SECONDS CHAT_MAX_TOKENS
  CHAT_DOCUMENT_MODEL CHAT_DOCUMENT_REASONING_EFFORT
  CHAT_VALIDATE_DOCUMENT_EVIDENCE CHAT_IDEMPOTENCY_CLAIMS_ENABLED
  CODE_API_FORMAT CODE_API_KEY CODE_BASE_URL CODE_MODEL
  CODE_MAX_OUTPUT_TOKENS CODE_TIMEOUT_SECONDS
  SMTP_HOST SMTP_PORT SMTP_TLS SMTP_SSL SMTP_USER SMTP_PASSWORD EMAILS_FROM_EMAIL
)
for release_config_var in "${release_config_vars[@]}"; do
  unset "$release_config_var"
done

release_compose=(docker compose --project-name "$release_project" --env-file "$release_env_file" -f "$release_root/compose.release.yml")
# Quiet validation never renders interpolated secrets to the terminal.
"${release_compose[@]}" config --quiet
case "$release_action" in
  check) printf '%s\n' 'Release configuration is valid.' ;;
  build) "${release_compose[@]}" build backend ;;
  up)
    "${release_compose[@]}" build backend
    "${release_compose[@]}" up -d --wait --wait-timeout 120 db
    "${release_compose[@]}" stop backend
    "${release_compose[@]}" run --rm --no-deps prestart
    "${release_compose[@]}" up -d --no-deps --wait --wait-timeout 120 backend
    "${release_compose[@]}" ps
    ;;
  status) "${release_compose[@]}" ps ;;
  stop) "${release_compose[@]}" stop backend db ;;
esac
