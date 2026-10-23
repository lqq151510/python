#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_dir="$project_dir/services/zebao-rag"
cd "$project_dir"

# An explicit Python may reuse an existing compatible environment. Only the
# imported source tree is added to PYTHONPATH; no original data is opened.
if [[ -z "${RAG_PYTHON:-}" ]]; then
  uv sync --project "$service_dir" --frozen --python 3.12
  RAG_PYTHON="$service_dir/.venv/bin/python"
fi
ZEBAO_RAG_WORKER_TOKEN="$(python3 - <<'PY'
from pathlib import Path
import os
import secrets

directory = Path('.local/zebao-rag')
directory.mkdir(parents=True, exist_ok=True)
path = directory / 'worker.token'
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        file.write(secrets.token_urlsafe(48))
except FileExistsError:
    pass
token = path.read_text().strip()
if len(token) < 32:
    raise SystemExit('Invalid local worker token file')
print(token)
PY
)"
export ZEBAO_RAG_WORKER_TOKEN
export PYTHONPATH="$service_dir/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
# Default to cached weights; opt in to a first download with HF_HUB_OFFLINE=0.
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
# gRPC (Milvus) has its own proxy rules. Keep local RPCs local while retaining
# the user's proxy for an explicitly requested model download.
export no_grpc_proxy="127.0.0.1,localhost,::1${no_grpc_proxy:+,$no_grpc_proxy}"
export no_proxy="127.0.0.1,localhost,::1${no_proxy:+,$no_proxy}"
export NO_PROXY="$no_proxy"
exec "$RAG_PYTHON" -m zebao_rag.worker --port "${RAG_WORKER_PORT:-8765}"
