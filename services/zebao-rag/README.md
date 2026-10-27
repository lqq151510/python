# zebao-rag in DevAssist

2026-10-16 从泽宝的本地 `zebao-rag` 导入，原实现由 Trae 完成，整合由开心（Codex）完成。
源文件的 SHA256 见 `UPSTREAM.json`；这是源码导入记录，不是原仓库的 Git 提交。

## 环境与启动

此目录是独立 Python 3.12 项目，DevAssist 后端仍使用自己的 Python 3.14 环境。

```bash
# 从 DevAssist 根目录运行，默认只读取已经缓存的模型权重
bash scripts/devassist-dev.sh --with-rag

# 也可以单独启动模型进程
bash scripts/rag-worker.sh
```

首次需要下载权重时，显式设置 `HF_HUB_OFFLINE=0`。模型下载需要可用的 Hugging Face 网络连接。
如果已安装兼容环境，可传 `RAG_PYTHON=/absolute/path/to/python` 复用依赖；
脚本把本目录 `src` 放在导入路径前面，不读取旧项目的数据。默认安装无需旧项目存在。

worker 只监听 `127.0.0.1:8765`，采用根目录 `.local/zebao-rag/worker.token` 的随机令牌鉴权。
令牌不进入前端。后端使用 `RAG_WORKER_ENABLED=true` 启用调用，混合检索在 worker 不可用时回退 BM25。

网页检索走 `/rank`：只接收后端按用户和文档权限筛出的文本片段，最多 256 块。
应用检索通过 Milvus 持久保存向量与 ID；Chroma 临时集合用于同模型同资料对照。
不向向量库写完整原文，PostgreSQL 是文档来源；进程另有最多 1024 个文档向量的内存缓存。服务串行推理，忙时返回 503。
查询使用文本检索指令，CLI 原有的图文检索指令仍保留。

## 保留的 CLI 能力

源码包含 `build/search/image/ask/chat/sessions/eval/eval-gen`。启动方式：

```bash
uv run --project services/zebao-rag zebao-rag sessions
```

多模态检索、Ollama Agent、会话 JSON 和摘要压缩仍属于本地 CLI。
网页当前接入的是 Markdown/TXT 文档的向量及混合检索；网页图片上传与持久化对话尚未接入。
CLI 按主题集合隔离，不能作为多用户 HTTP 接口直接暴露。

本次没有导入原项目的 `data/`、`chroma_db/`、`.venv/`、权重或含个人内容的脚本。
CLI 的数据路径可用 `ZEBAO_RAG_DATA_DIR`、`ZEBAO_RAG_CHROMA_DIR` 配置，
生成模型可用 `OLLAMA_BASE_URL`、`ZEBAO_RAG_AGENT_MODEL` 配置。
评估与固定演示中的来源文件名依赖原语料；缺少语料时不能复现 Trae 的评估数字。
不要把旧评估数字当作网页整合后的成绩。

## 嵌入模型 LoRA 微调

编码器（`Qwen/Qwen3-VL-Embedding-2B`）支持用 LoRA adapter 做领域微调，
完整的数据构造、训练参数、前后对比数字和已知边界见
[../../docs/EMBEDDING_FINETUNE.md](../../docs/EMBEDDING_FINETUNE.md)。

设置 `ZEBAO_RAG_ADAPTER=/path/to/adapter` 后 `embedder.get_model()` 会把 adapter
装到基座上；**切换后要重建向量索引**，否则库里的旧向量和新查询向量不在同一空间。

## 验证

```bash
uv run --project services/zebao-rag python -m unittest discover -s services/zebao-rag/tests -v
```

这些测试采用真实内存 Chroma 和确定性模拟向量，验证鉴权、参数、缓存限长和候选隔离，
不会下载权重、调用 Ollama 或打开原个人资料。


## 五库学习功能

默认应用向量库为 Milvus；`/learning` 发起两库对照并保存结果到 MySQL。
Redis 缓存命中 ID 与分数，有效期 180 秒。职责、练习和已知边界见根目录 `docs/LEARNING_DATABASES.md`。
Milvus 服务为 2.4 系列，Python SDK 与 Setuptools 依赖已在本模块锁文件中固定兼容版本。
`no_grpc_proxy` 只为模型进程追加本机地址，避免 gRPC 被全局代理转发。

### 本地代码生成 CLI

独立项目的代码生成命令已并入此目录。启动本机 Ollama 并准备模型后，可从 DevAssist 根目录运行：

```bash
uv run --project services/zebao-rag zebao-rag code "用 FastAPI 写健康检查接口"
```

默认模型为 `mellum2.1-q6k:latest`，可通过 `ZEBAO_RAG_CODE_MODEL` 更改；服务地址使用 `OLLAMA_BASE_URL`。该命令仅返回文本，不操作文件或执行命令，与网页编程工作台的 `CODE_*` 配置相互独立。模型是否可用需以本机实际安装状态为准。
