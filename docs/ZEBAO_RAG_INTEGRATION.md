# zebao-rag 接入 DevAssist（2026-10-16）

负责人：开心（Codex）。原 zebao-rag 由 Trae 开发，泽宝通过转发消息协调。
本次交付包含源码导入、网页文档检索，以及泽宝随后确认的五库学习功能。未提交、未推送、未发布。
最新数据库职责与练习见 [五种数据库练习](LEARNING_DATABASES.md)。

## 已实现

- `services/zebao-rag` 导入原项目的 11 个 Python 模块、依赖锁和 Python 3.12 配置。
  `UPSTREAM.json` 保存导入前 SHA256；原目录中的这些文件逐一复核未改变。
- DevAssist 仍使用 Python 3.14，模型进程用独立 Python 3.12 环境，默认安装不依赖原项目路径。
- 知识库和问答页可选关键词、语义、混合检索。默认请求保持原 BM25 契约。
- 混合检索用 RRF 合并两个排名，避免直接相加 BM25 分数与余弦相似度。
- 来源继续使用 PostgreSQL 中的文档 ID、片段 ID、标题、行号和原文，原文预览与范围检索保持可用。
- 查询先在 SQL 中验证用户归属和指定文档，再交给 worker；worker 返回的 ID 必须属于当前候选。
- worker 只监听本机回环地址，使用本地随机令牌；后端 RPC 禁用环境代理、重定向和自动重试。
- 应用向量在 Milvus 中持久保存，检索限定当前用户与当前候选；Chroma 对照集合在 finally 中释放。
  模型进程仅缓存最多 1024 个文档向量，不把完整原文写进向量库，不打开原项目个人资料。

模型仍为 `Qwen/Qwen3-VL-Embedding-2B`。网页的文搜文使用单独的文本检索指令，
CLI 原文搜图指令保留。任务指令按场景定制的依据见
[Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)。

## 启动

从 DevAssist 根目录运行：

```bash
bash scripts/devassist-dev.sh --with-rag
```

该命令启动本项目的 PostgreSQL、向量 worker、后端与前端，worker 就绪后再启动应用。
Ctrl+C 结束脚本启动的进程，数据库和数据卷保留。模型默认只使用本机缓存。
首次没有权重时可显式设置 `HF_HUB_OFFLINE=0` 允许下载，需有可用的 Hugging Face 网络连接。

也可单独运行 `bash scripts/rag-worker.sh`，并在后端配置 `RAG_WORKER_ENABLED=true`。
`.local/zebao-rag/worker.token` 由脚本自动生成，权限 600，不要复制到前端或聊天。
`RAG_PYTHON=/absolute/path/to/python` 可选择已有兼容环境；默认使用模块自己的 `.venv`。

默认端口：前端 5173、后端 8000、worker 8765。
如需修改 worker 端口，完整启动脚本支持 `RAG_WORKER_PORT`；手动分开启动时，
后端还需要匹配设置 `RAG_WORKER_URL`，只允许本机 HTTP 根地址。

## 接口变化

`POST /api/v1/documents/search` 新增可选 `method: "bm25" | "vector" | "hybrid"`，默认 `bm25`。
成功响应中的 `method` 是实际使用方式，`warning` 为可选降级原因。

`POST /api/v1/chat` 新增可选 `retrieval_method`，非 BM25 时要求 `use_documents: true`。
知识库回答返回实际 `retrieval_method` 和可选 `retrieval_warning`，普通单轮问答保持原响应。

worker 禁用、超时、忙碌、参数或输出异常时，混合模式回退 BM25 并标记 warning；
纯语义模式返回明确 503。指定文档不存在、不属于用户或已删除时，仍先返回 404，
不会尝试其他文档或原项目集合。无匹配片段时保持知识库问答的拒绝生成逻辑。

## 实际验证

| 验证 | 结果 | 证据边界 |
|---|---|---|
| 后端全套回归 | 158 passed，13.62 秒 | 独立临时 PostgreSQL/MySQL；包含五库与真实 worker 联调；临时数据库结束后移除 |
| worker 单元与 HTTP 测试 | 4 passed | 真实内存 Chroma、模拟向量；鉴权、候选隔离、缓存限长、参数验证 |
| 浏览器回归 | 26 passed，24.7 秒 | Chrome；接口模拟，覆盖检索、来源操作、学习记录和两库对照页面 |
| 前端生产构建 | 通过 | TypeScript + Vite |
| 后端静态检查 | Ruff 通过；目标模块 mypy 通过 | semantic、knowledge、chat route 及所依赖的接口类型 |
| 真实 Qwen 编码冒烟 | 通过 | 3 段合成资料；登录凭据失效问题的首条结果是令牌过期说明 |
| 原项目保护 | 14 个导入来源文件哈希均未改变 | 不包括随后按协作约定追加的 COLLABORATION.md 回执 |
| 本地 CLI 基本启动 | `sessions` 正常，显示无会话 | 新环境独立可启动，未导入原会话 |

真实冒烟首次请求为 1.20 秒，命中说明的余弦相似度约 0.713。这个结果只证明小样例链路，
不构成检索质量评估。脱敏输出保存在 `.local/zebao-rag/real-worker-smoke.json`。

可复现命令：

```bash
# worker 先启动并就绪，再从 backend/ 运行
VERIFY_MULTIDB=1 VERIFY_REAL_RAG=1 RAG_WORKER_ENABLED=true RAG_CACHE_ENABLED=true uv run --frozen python scripts/test-local.py

# 从根目录运行
uv run --project services/zebao-rag --frozen python -m unittest discover -s services/zebao-rag/tests -v

# 前端先在 IPv4 回环地址启动；从 frontend/ 运行
PLAYWRIGHT_BASE_URL=http://127.0.0.1:5173 PLAYWRIGHT_BROWSER_CHANNEL=chrome bunx --no-install playwright test -c playwright.knowledge.config.ts --workers=1
bun run build
```

验证中修复了 worker 令牌文件的创建方式、显式离线加载模型、返回值类型检查等问题。
最初浏览器测试因页面加载超时出现 2 项失败，预热服务后以单 worker 完整复跑通过。
后端仍有一条既有 Starlette TestClient 弃用警告，本轮未改动测试客户端依赖。

## 本轮限制与后续

1. 本轮导入时，网页只处理 Markdown/TXT。当前应用已在 `/images` 提供图片上传、文搜图和以图搜图。
2. Trae 的 Ollama Agent、文件会话与摘要代码已导入 CLI，尚未改成多用户网页会话。
3. 没有重跑真实 DeepSeek 生成、真实 Ollama 对话、原个人资料集或原项目 24/8 条评估。
   原回执数字保留为历史资料，不能作为当前网页成果。
4. 一个向量请求最多 256 个候选片段，超过时需选定较小文档范围。混合模式会回退并显示原因。
5. `RAG_MIN_SIMILARITY=0.3` 是当前阈值，尚未用独立无答案集校准；高相似度不等于足够证据。
6. 首次导入使用主项目现有分块与行号体系；日语表格切块问题尚未专门改进。
7. 当前 Milvus 保存应用向量，Chroma 采用临时对照索引。历史向量物理清理、跨库账号删除和更大规模增量索引仍需完善。

## 文件与回滚

新目录：`services/zebao-rag/`。新增后端适配器 `backend/app/semantic.py`、
前端 `RetrievalMethod.tsx`、启动脚本 `scripts/rag-worker.sh` 与相关测试。
现有知识库、聊天接口、配置、网页、启动脚本和文档做了局部修改。
根 workspace 排除此独立模型项目；Git 与 Docker 上下文排除运行数据。

原有未提交工作树保留。本轮修改前的相关文件副本在 `.local/zebao-integration-before/`；
恢复前要检查是否出现后续编辑，不能直接覆盖。停用集成可将 `RAG_WORKER_ENABLED=false`，
网页使用关键词检索，并按原命令启动应用；没有新增数据库迁移或修改表结构。

两边交接以根目录 `COLLABORATION.md` 和原项目同名文件的最新回执为准。


## 五库追加范围

新增 MySQL 学习记录、Redis 检索缓存和计数、Milvus 应用向量，学习实验页 `/learning` 可将两库对照结果保存为学习记录。
本机 MySQL 学习表已初始化；测试采用随机命名的独立 MySQL 数据库，结束后只删除本次测试创建的数据库。
Redis 采用独立逻辑库与命名前缀，不执行 flush；Milvus 不读取或覆盖其他项目的集合。


最终启动验收：`bash scripts/devassist-dev.sh --with-rag` 已完整跑通；实际 HTTP 的健康检查、登录、文档列表、MySQL 学习记录列表返回 200，Redis 状态可用。使用 `http://localhost:5173/learning`，与后端允许的浏览器来源一致。最新后端回归为 158 passed（12.32 秒），浏览器为 26 passed；真实生成模型仍未验。脱敏验收摘要在 `.local/zebao-rag/final-validation.json`。
