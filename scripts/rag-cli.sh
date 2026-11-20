#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_dir="$project_dir/services/zebao-rag"

if [[ $# -eq 0 || "$1" == "--help" || "$1" == "-h" ]]; then
  cat <<'HELP'
DevAssist 本地 RAG 命令行

用法：bash scripts/rag-cli.sh <子命令> [参数...]
示例：
  bash scripts/rag-cli.sh sessions
  bash scripts/rag-cli.sh search "查询内容"
  bash scripts/rag-cli.sh code "用 FastAPI 写健康检查接口"

子命令：build、build-personal、build-japanese、search、search-personal、
        search-japanese、image、verify、demo、ask、code、chat、sessions、eval、eval-gen

固定使用本项目 services/zebao-rag 源码与独立 Python 3.12 环境。
可用 RAG_PYTHON 指定兼容解释器的绝对路径。默认仅使用缓存模型；
首次下载须显式设置 HF_HUB_OFFLINE=0。本入口不自动安装或同步依赖，
也不读取旧项目归档；数据路径仍由 ZEBAO_RAG_DATA_DIR / ZEBAO_RAG_CHROMA_DIR 配置。
HELP
  exit 0
fi

rag_python="${RAG_PYTHON:-$service_dir/.venv/bin/python}"
if [[ "$rag_python" != /* ]]; then
  printf 'RAG_PYTHON 必须是兼容解释器的绝对路径。\n' >&2
  exit 2
fi
if [[ ! -x "$rag_python" || -d "$rag_python" ]]; then
  printf 'RAG Python 解释器不可执行：%s\n' "$rag_python" >&2
  printf '请从 DevAssist 根目录手动准备环境：\n  uv sync --project services/zebao-rag --frozen --python 3.12\n' >&2
  exit 2
fi

cd "$project_dir"
export PYTHONPATH="$service_dir/src${PYTHONPATH:+:$PYTHONPATH}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"
exec "$rag_python" -m zebao_rag.cli "$@"
