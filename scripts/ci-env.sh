#!/usr/bin/env bash
# CI 占位配置。真实密钥只留在未提交的 .env，这里的值不能用于部署。
set -euo pipefail

cat <<'EOF'
FASTAPI_ENV=development
PROJECT_NAME=DevAssist
SECRET_KEY=ci-only-not-a-secret
FIRST_SUPERUSER=admin@example.com
FIRST_SUPERUSER_PASSWORD=ci-only-password
POSTGRES_PASSWORD=ci-only-db-password
DATABASE_URL=postgresql://postgres:ci-only-db-password@localhost:5432/app
SMTP_HOST=localhost
EMAILS_FROM_EMAIL=noreply@example.com
SMTP_TLS=false
SMTP_PORT=1025
EOF
