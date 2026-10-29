# DevAssist

已接入 Trae 开发的 zebao-rag 源码，位于 `services/zebao-rag`。知识库和问答页新增关键词、语义、混合检索；
使用 `bash scripts/devassist-dev.sh --with-rag` 启动本机向量服务。具体实现、验证边界和协作交接见
[整合记录](docs/ZEBAO_RAG_INTEGRATION.md) 与 [COLLABORATION.md](COLLABORATION.md)。
网页已接入图片上传、文字搜图、以图搜图和独立的多轮资料 Agent（`/images`、`/agent`）。
RAG 问答的历史记录与多轮 Agent 会话分别保存，二者的上下文行为不同。

新增“学习实验”页 `/learning`：PostgreSQL 保存用户和文档，MySQL 保存学习/实验记录，Redis 缓存检索，
Milvus 提供应用向量检索，Chroma 用于同模型同资料的本地对照。入门练习、真实边界和验收方法见
[五种数据库练习](docs/LEARNING_DATABASES.md)。这是分阶段学习项目，数据库数量不代表已经掌握的能力。

项目知识与故障排查助手，服务于 Python AI 应用与智能体开发学习、作品集和面试准备。

账号切换、代码写入回滚、向量分批和 AIGC 启动保护的当前实现与验证边界见
[可靠性修复记录](docs/RELIABILITY_FIXES.md)。
2026-10-23 的 BM25 排序优化、独立留出集结果和 Agent 页面状态修复见
[本轮优化记录](docs/RAG_OPTIMIZATION.md)。
2026-10-26 新增默认关闭的回答证据校验实验与独立评估工具；现有 1.5B 模型未通过资格验证，
没有默认启用或宣称拒答提升，见 [资料不足与引用校验记录](docs/GROUNDED_ANSWERS.md)。

聊天工作台现在分为 RAG 知识问答与编程智能体两种模式。RAG 保留 Markdown/TXT 知识库、关键词/语义/混合检索和来源片段，成功回答与完整引用按账号持久保存；每次生成仍按单轮请求处理，不自动把历史回答加入上下文。编程模式提供按账号隔离的应用沙箱、文件附件、Goal/Plan、候选改动预览与权限控制。独立 `/agent` 页面保留最近四轮上下文，只提供当前用户资料的只读检索工具，最多执行五步。

浅色界面已接入真实主应用：问答页提供独立来源面板和“只检索原文”按钮，知识库页可以将检索问题带到问答页。不填写模型密钥也能完成文档上传、持久保存、检索与原文核对。实际验收和回滚范围见 [浅色界面接入记录](docs/LIGHT_UI.md)。

知识库支持预览已存储的文档片段、按指定文档检索；问答来源可打开对应文档并定位片段。检索范围会随问题传递至问答页，并由后端校验文档归属。实现边界和最新验收见 [文档预览与范围检索](docs/DOCUMENT_TOOLS.md)。

## 开源来源与新增功能

底座是 [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template)，固定提交 `e99fbaeba3b55190156ebae525ad9d481d76677e`。保留 [MIT 许可证](LICENSE)、原有 Git 历史和 [模板 README](docs/TEMPLATE_README.md)。登录、数据库模型和组件来自模板；聊天接口、模型适配、请求标识、聊天页面及对应测试是本项目新增内容。

- 后端：Python 3.14、FastAPI、Pydantic、SQLModel、OpenAI 异步 SDK。
- 前端：React、TypeScript、Vite、TanStack Router/Query，使用 Bun。
- 数据库：本地 Docker PostgreSQL 16 Alpine，独立 `devassist` Compose 项目和持久化卷。采用已有镜像减少首次下载，并同步调整了对应的数据目录。
- 生成模型：DeepSeek 官方 `deepseek-flash`，普通 JSON、非思考模式，输出上限 1024 tokens。
- 编程模型：服务端可配置 OpenAI Chat Completions 兼容、OpenAI Responses、Anthropic Messages、Gemini `generateContent` 接口；模式各自解析请求和响应格式，不在浏览器保存 API 密钥。

## 编程工作台与模型配置

默认在三项代码模型配置都为空时，复用已有 DeepSeek Chat Completions 配置。要换服务，在根目录 `.env` 设置 `CODE_API_FORMAT`、`CODE_BASE_URL` 和 `CODE_MODEL`；第三方服务再设置 `CODE_API_KEY`。配置示例见 `.env.example`。本地 Ollama 使用 `openai_chat_completions` 和 `http://127.0.0.1:11434/v1`，密钥可以留空。第三方 OpenAI 兼容服务同样用这个格式，但必须在 `.env` 填写自己的密钥。Responses、Anthropic Messages 和 Gemini 仍然可用。项目只调用文本生成接口，不启用模型侧工具。

编程工作区只按当前登录账号读写应用维护的目录。文件可以来自上传、公开 GitHub 仓库导入，或在页面里新建、编辑和删除。上传上限为每个文件 512 KiB、每账号 50 个文件/4 MiB；每次模型请求最多读取 20 个勾选文件、总计 64 KiB。批量应用模型候选改动时，界面会检查权限和确认状态；服务端校验每个文件的预览版本，发生冲突时整批拒绝。应用进程在支持文件锁的本地文件系统上协调写入；直接修改磁盘的外部程序不受此锁保护。用户仍可手动编辑沙箱文件；版本校验不证明已完整审阅差异。完整权限仅涵盖应用沙箱；本版本没有终端命令执行能力，也不会读取用户主机上的项目目录。

| `CODE_API_FORMAT` | 服务端请求格式 | 说明 |
|---|---|---|
| `openai_chat_completions` | `/v1/chat/completions` | 默认兼容格式。三项都空时复用 `DEEPSEEK_*`；本机 Ollama 可只填地址和模型名；第三方必须另填 `CODE_API_KEY` |
| `openai_responses` | `/v1/responses` | 可设置 `CODE_BASE_URL=https://api.openai.com/v1` |
| `anthropic_messages` | `/v1/messages` | 默认指向 Anthropic API |
| `gemini_generate_content` | `/v1beta/models/{model}:generateContent` | 通过 `x-goog-api-key` 传入服务端密钥 |

第三方兼容代理的 `CODE_BASE_URL` 由管理员配置，必须是 API 根地址，不能带密钥或查询参数。环境配置修改后重启后端。

## 本地启动

需要 Python 3.14、uv、Bun 和已运行的 Docker Desktop。以下命令从项目根目录运行。

```bash
python3 scripts/setup-local.py
```

脚本只在 `.env` 不存在时生成本地数据库密码、管理员密码和应用密钥，不覆盖已有配置、不输出密码。打开根目录 `.env`，在本地填写 `DEEPSEEK_API_KEY` 后保存。不要把密钥贴进聊天、源码或 `VITE_` 前端变量。

```bash
bash scripts/devassist-dev.sh
```

脚本初始化数据库并启动前后端；Ctrl+C 只结束其启动的开发进程，数据库与数据卷保留。

- 知识库页面：http://localhost:5173/knowledge
- 聊天页面：http://localhost:5173/chat
- 学习笔记：http://localhost:5173/notes
- 复习清单：http://localhost:5173/tasks
- 个人中心：http://localhost:5173/settings
- 图片检索：http://localhost:5173/images
- 接口文档：http://127.0.0.1:8000/docs
- OpenAPI 描述：http://127.0.0.1:8000/api/v1/openapi.json
- 模板存活检查：`GET /api/v1/utils/health-check/`
- 管理员账号：`admin@devassist.example.com`；密码在本地 `.env` 的 `FIRST_SUPERUSER_PASSWORD` 字段中。

修改 `.env` 后重启后端。没有模型密钥时仍可登录、导入资料并点击“只检索原文”；发送问题生成回答会返回明确的 503 提示。真实模型调用仍需单独验收。

开发服务监听回环地址；首阶段不包含公网部署。数据库映射为 `127.0.0.1:5432`。容器 backend 通过 Compose environment 注入配置，不把 `.env` 复制进镜像。容器默认使用一个 API worker；启用 AIGC 时，启动恢复前还会获取 PostgreSQL 会话独占锁，第二个执行器会拒绝启动，防止重排存活进程的任务。所有 AIGC 实例必须连接同一个应用数据库，详见 [AIGC 部署约束](docs/AIGC.md)。

## 接口契约

`POST /api/v1/chat` 需要模板的 Bearer Token。请求 `{ "message": "你的问题" }`，去除首尾空白后限定 1～4000 个 Unicode 字符。可选字段 `use_documents: true` 启用文档检索；默认仍为普通问答。

知识库模式可附加 `document_id`（UUID）只检索该文档；该字段必须与 `use_documents: true` 一起使用。不存在、已删除或不属于当前用户的文档返回 404，指定文档没有命中时返回 `no_document_evidence`，不会扩大为全库检索。

普通问答成功返回 `{ "answer": "最终答案", "request_id": "请求标识" }`；文档模式还返回 `sources`，包含文件名、标题、行号、原文片段与引用编号。所有该接口的响应都有 `X-Request-ID`，包括未登录和校验失败；CORS 允许前端读取该标头。

输入错误沿用 FastAPI 422 格式。模型错误返回 `detail: { code, message }`，不会直接暴露供应商原始异常。

编程工作台接口也需要登录，服务端从当前用户 ID 选择专属沙箱目录：

| 接口 | 功能 |
|---|---|
| `GET /api/v1/code/config` | 返回非敏感的代码模型格式、模型名和配置状态 |
| `GET /api/v1/code/workspace` | 列出当前用户的沙箱文件 |
| `GET /api/v1/code/workspace/files/{path}` | 读取当前用户沙箱中的受限文本文件 |
| `PUT /api/v1/code/workspace/files` | 新建或保存当前账号沙箱中的一个文本文件 |
| `DELETE /api/v1/code/workspace/files/{path}` | 删除当前账号沙箱中的一个文本文件 |
| `POST /api/v1/code/workspace/files` | multipart `files` 上传，最多 10 个代码/文本附件 |
| `POST /api/v1/code/workspace/github` | 导入一个公开 GitHub 仓库的默认分支文本文件 |
| `POST /api/v1/code/workspace/diff` | 按行对照候选内容与当前沙箱文件 |
| `POST /api/v1/code/generate` | 根据 Goal、任务和选中文件生成计划及候选改动 |
| `POST /api/v1/code/workspace/apply` | 每项携带差异预览返回的 `base_version`；文件变化时返回 `409 workspace_conflict`，整批不写入 |

上传及模型生成均有请求体、文件数、上下文长度、输出文件数和超时上限。所有接口只操作当前账号的沙箱；模型服务 API Key 不返回给前端。

| 条件 | HTTP 状态 |
|---|---|
| 未登录 | 401 |
| 空白、超长或类型错误 | 422 |
| 文档模式没有匹配片段 | 422，`no_document_evidence`，不调用模型 |
| 缺少模型配置、上游认证失败 | 503 |
| 上游限流 | 429 |
| 整体时间预算耗尽或请求超时 | 504 |
| 连接失败、上游错误、无有效答案 | 502 |

模型调用总预算为 30 秒，包含最多两次请求、重试等待和客户端清理；连接超时为 5 秒。SDK 自动重试关闭，应用仅对连接建立失败、429、5xx 最多重试一次；认证失败、参数错误和读取超时不重试。再次点击发送属于新请求。

所有 `/api/v1` 接口现在提供 `X-Request-ID`；结构化请求日志记录路由模板、方法、响应状态、耗时、错误码和异常类型，向量检索日志使用同一请求编号。日志不记录问题、回答、文件名、查询参数、密钥或供应商异常正文。默认 Sentry 配置为空。字段、日志汇总命令和隔离故障验收见 [请求追踪与故障检查](docs/OBSERVABILITY.md)。

## 文档导入与检索

接口均要求登录，管理员也只能访问自己的知识库：

| 接口 | 功能 |
|---|---|
| `GET /api/v1/documents` | 文档列表，最多返回 20 条 |
| `GET /api/v1/documents/{id}` | 自己的文档元信息及按位置排序的已存储片段 |
| `POST /api/v1/documents` | multipart 表单 `file` 上传；新增返回 201，同内容复用返回 200 |
| `POST /api/v1/documents/search` | JSON `{ "query": "关键词", "limit": 5 }`，可附加 `document_id` 限定范围，返回来源片段 |
| `DELETE /api/v1/documents/{id}` | 删除自己的文档及其分块 |

支持 UTF-8 的 `.md` / `.txt`，单文件最多 256 KiB、120000 字符、256 个分块，每人最多 20 份文档。上传请求体另有 320 KiB 上限，包含 multipart 元数据，也检查没有 Content-Length 的流式请求。来源行号对应换行规范化后的原文。检索请求可用 `method: "vector"` 或 `"hybrid"`；默认 `"bm25"`。向量模式按每批最多 256 个片段处理，最多覆盖 5120 个已授权片段，合并全局前 K 条结果，全部批次共用一次超时预算。任一批次失败不会返回不完整的向量结果；混合模式仍会回退并返回 `warning`。

BM25 是本阶段的关键词检索基线，中文使用相邻双字片段分词，英文按词匹配。它不等同于向量语义检索：同义表达、缩写和复杂语义可能漏检。原文只作为参考数据，不执行其中的命令；生成回答的引用列表表示本次检索来源，回答忠实度仍需要真实模型评估。

示例文档位于 `examples/knowledge/`，检索评估用例位于 `examples/retrieval-eval.json`。详细范围、实测结果和学习练习见 [第二阶段说明](docs/PHASE2.md)。

检索评估已升级为片段级：使用 5 份合成资料、30 个独立标注问题，按文件名、证据行号与原文引用共同判定。2026-10-23 仅将 BM25 的长度归一化参数 `b` 从 0.75 调整为 0.5，`k1=1.5` 不变：既有诊断集严格通过 20/30 → 22/30，首条命中率 72.7% → 81.8%，前五条平均证据召回率 88.6% → 90.9%；8 个无答案问题中仍有 4 个返回片段。原语料、诊断标注和回归门槛均未改动。

另以独立编写并冻结的 24 题留出集复验，新旧参数均严格通过 14/24：14 个有答案问题的首条、召回和 MRR 均为 100%，10 个无答案问题全部返回片段。当前只能确认已知诊断集的长文排序改善，没有泛化提升证据。过程、对照指标与复现命令见 [本轮优化记录](docs/RAG_OPTIMIZATION.md)，完整失败样例见 [留出集评估报告](docs/RETRIEVAL_HOLDOUT_REPORT.md)。当前完整诊断结果见 [最新诊断报告](docs/RETRIEVAL_EVAL_REPORT.md)，历史 20/30 汇总保留在 [评估使用说明](docs/RETRIEVAL_EVAL.md)。

## 验证

后端测试需要运行中的本地 PostgreSQL。使用项目的隔离脚本新建临时数据库、执行迁移和测试，结束后只移除本次创建的测试数据库，保留应用账号和文档。模板的原始 pytest fixture 会清理它所连接的数据库，因此不要直接对应用数据库运行完整测试。

也可从仓库根目录运行 `.venv/bin/python scripts/verify-isolated.py`：使用缓存的 PostgreSQL
镜像创建一次性独立数据库，完成迁移和完整后端回归后，启动临时 API 验证双账号隔离、
真实本地模型问答、历史幂等和代码工作区，再清理本次临时资源。需要 Docker 已运行和现有
`qwen2.5-1.5b-instruct:latest` 模型；可用 `--model` 指定已安装模型，或 `--skip-runtime`
仅运行迁移和后端回归。该脚本不读取项目 `.env`、不自动下载、不改原数据库与数据卷。
2026-10-26 最新完整回归为 348 项通过、2 项跳过、1 条既有 Starlette/httpx 弃用警告，
运行时 23 项检查通过，使用临时 PostgreSQL 与本机已有 Ollama 模型；这些运行检查与
独立回答质量实验分开报告，详见 [资料不足与引用校验记录](docs/GROUNDED_ANSWERS.md)。
2026-10-23 的 262 项验收保留在 [排序与页面优化记录](docs/RAG_OPTIMIZATION.md)，此前 260 项保留在
[可靠性修复记录](docs/RELIABILITY_FIXES.md)。

```bash
cd backend
uv run --frozen python scripts/test-local.py
```

片段级检索评估可单独运行，不需要 `.env`、模型密钥、数据库或网络连接。从 `backend/` 执行：

```bash
uv run --frozen python scripts/evaluate-retrieval.py
```

生成 `.local/retrieval-eval.json` 和 `docs/RETRIEVAL_EVAL_REPORT.md`。默认成功生成报告返回 0，即使检索样例存在失败；加 `--strict` 要求每个样例都满足首条正确、完整召回或无答案返回空结果，当前基线会返回 1。无效数据返回 2，区分评分结果与脚本故障。

前端构建与独立知识库、Agent UI 模拟测试：

```bash
cd frontend
bun run build
PLAYWRIGHT_BROWSER_CHANNEL=chrome bunx --no-install playwright test -c playwright.knowledge.config.ts
PLAYWRIGHT_BROWSER_CHANNEL=chrome bunx --no-install playwright test -c playwright.agent.config.ts
```

独立 UI 测试使用模拟接口，只证明页面行为。真实登录、PostgreSQL 联调与真实 DeepSeek 调用须单独验证；缺少密钥时不会把模拟答案计作真实模型成果。

真实模型问答已经用本机 Ollama 的 OpenAI 兼容端点补验一次（模型不是 DeepSeek 云端）：登录、上传、检索、生成、落库、历史接口全部走通，但 1.5B 模型存在引用写错和越界问题被误答的情况。命令、逐条结果和限制见 [真实模型问答验证](docs/LLM_CHAT.md)。

```bash
export SMOKE_PASSWORD='换成你的本地测试密码'
cd backend
# 后端需先按该文档注入 DEEPSEEK_BASE_URL/DEEPSEEK_MODEL 等环境变量
../.venv/bin/python scripts/smoke-llm-chat.py \
  --email llm-smoke@example.com --password "$SMOKE_PASSWORD" \
  --doc ../docs/VERIFICATION.md --doc ../docs/PHASE2.md \
  --question '小型人工检索评估一共用了多少个样例？' --methods bm25
```

AIGC 生成页的浏览器验证（需要假 ComfyUI、后端、前端三个进程，完整步骤见 [docs/AIGC.md](docs/AIGC.md)）：

```bash
cd frontend
PLAYWRIGHT_BROWSER_CHANNEL=chrome PLAYWRIGHT_BASE_URL=http://localhost:5173 \
  bunx --no-install playwright test -c playwright.aigc.config.ts
```

本地 `.env` 已排除于 Git 与根目录、backend、frontend 的容器构建上下文。只提交空密钥配置示例 `.env.example`。实际结果见 [验收记录](docs/VERIFICATION.md)。

## 学习与后续

阅读 [第一阶段学习说明](docs/LEARNING.md)、[第二阶段说明](docs/PHASE2.md) 与 [第三阶段整合记录](docs/ZEBAO_RAG_INTEGRATION.md)，理解请求链和文档解析、分块、检索的区别。真实模型回答已用本地小模型补验；网页已有多模态检索和受控只读 Agent。下一步应复测更高质量模型的引用与拒答、扩大检索评估集，并完成新增能力的数据库、真实模型与浏览器联合验收。

本机 Docker 已有另一项服务使用 IPv6 的 8000 端口，因此本项目明确使用 IPv4 API 地址 `http://127.0.0.1:8000`，前端仍为 `http://localhost:5173`。
