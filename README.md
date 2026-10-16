# DevAssist

已接入 Trae 开发的 zebao-rag 源码，位于 `services/zebao-rag`。知识库和问答页新增关键词、语义、混合检索；
使用 `bash scripts/devassist-dev.sh --with-rag` 启动本机向量服务。具体实现、验证边界和协作交接见
[整合记录](docs/ZEBAO_RAG_INTEGRATION.md) 与 [COLLABORATION.md](COLLABORATION.md)。
多模态检索与有记忆 Agent 的 CLI 代码已导入，网页图片上传和持久化对话尚未接入。

新增“学习实验”页 `/learning`：PostgreSQL 保存用户和文档，MySQL 保存学习/实验记录，Redis 缓存检索，
Milvus 提供应用向量检索，Chroma 用于同模型同资料的本地对照。入门练习、真实边界和验收方法见
[五种数据库练习](docs/LEARNING_DATABASES.md)。这是分阶段学习项目，数据库数量不代表已经掌握的能力。

项目知识与故障排查助手，服务于 Python AI 应用与智能体开发学习、作品集和面试准备。

第一阶段实现登录后的单轮模型问答。第二阶段新增 Markdown/TXT 导入、按用户隔离的文档知识库、BM25 关键词检索和来源片段展示；聊天页可选择将检索资料提供给模型。第三阶段接入本机向量与混合检索，已验证真实本地 Embedding 调用；真实生成模型验收仍暂缓，网页问答保持单轮，不持久化聊天记录。

浅色界面已接入真实主应用：问答页提供独立来源面板和“只检索原文”按钮，知识库页可以将检索问题带到问答页。不填写模型密钥也能完成文档上传、持久保存、检索与原文核对。实际验收和回滚范围见 [浅色界面接入记录](docs/LIGHT_UI.md)。

知识库支持预览已存储的文档片段、按指定文档检索；问答来源可打开对应文档并定位片段。检索范围会随问题传递至问答页，并由后端校验文档归属。实现边界和最新验收见 [文档预览与范围检索](docs/DOCUMENT_TOOLS.md)。

## 开源来源与新增功能

底座是 [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template)，固定提交 `e99fbaeba3b55190156ebae525ad9d481d76677e`。保留 [MIT 许可证](LICENSE)、原有 Git 历史和 [模板 README](docs/TEMPLATE_README.md)。登录、数据库模型和组件来自模板；聊天接口、模型适配、请求标识、聊天页面及对应测试是本项目新增内容。

- 后端：Python 3.14、FastAPI、Pydantic、SQLModel、OpenAI 异步 SDK。
- 前端：React、TypeScript、Vite、TanStack Router/Query，使用 Bun。
- 数据库：本地 Docker PostgreSQL 16 Alpine，独立 `devassist` Compose 项目和持久化卷。采用已有镜像减少首次下载，并同步调整了对应的数据目录。
- 生成模型：DeepSeek 官方 `deepseek-flash`，普通 JSON、非思考模式，输出上限 1024 tokens。

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
- 接口文档：http://127.0.0.1:8000/docs
- OpenAPI 描述：http://127.0.0.1:8000/api/v1/openapi.json
- 模板存活检查：`GET /api/v1/utils/health-check/`
- 管理员账号：`admin@devassist.example.com`；密码在本地 `.env` 的 `FIRST_SUPERUSER_PASSWORD` 字段中。

修改 `.env` 后重启后端。没有模型密钥时仍可登录、导入资料并点击“只检索原文”；发送问题生成回答会返回明确的 503 提示。真实模型调用仍需单独验收。

开发服务监听回环地址；首阶段不包含公网部署。数据库映射为 `127.0.0.1:5432`。容器 backend 通过 Compose environment 注入配置，不把 `.env` 复制进镜像。

## 接口契约

`POST /api/v1/chat` 需要模板的 Bearer Token。请求 `{ "message": "你的问题" }`，去除首尾空白后限定 1～4000 个 Unicode 字符。可选字段 `use_documents: true` 启用文档检索；默认仍为普通问答。

知识库模式可附加 `document_id`（UUID）只检索该文档；该字段必须与 `use_documents: true` 一起使用。不存在、已删除或不属于当前用户的文档返回 404，指定文档没有命中时返回 `no_document_evidence`，不会扩大为全库检索。

普通问答成功返回 `{ "answer": "最终答案", "request_id": "请求标识" }`；文档模式还返回 `sources`，包含文件名、标题、行号、原文片段与引用编号。所有该接口的响应都有 `X-Request-ID`，包括未登录和校验失败；CORS 允许前端读取该标头。

输入错误沿用 FastAPI 422 格式。模型错误返回 `detail: { code, message }`，不会直接暴露供应商原始异常。

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

日志只记录请求标识、时长、错误码和用量，不记录完整问题、回答、密钥或供应商异常正文。默认 Sentry 配置为空。

## 文档导入与检索

接口均要求登录，管理员也只能访问自己的知识库：

| 接口 | 功能 |
|---|---|
| `GET /api/v1/documents` | 文档列表，最多返回 20 条 |
| `GET /api/v1/documents/{id}` | 自己的文档元信息及按位置排序的已存储片段 |
| `POST /api/v1/documents` | multipart 表单 `file` 上传；新增返回 201，同内容复用返回 200 |
| `POST /api/v1/documents/search` | JSON `{ "query": "关键词", "limit": 5 }`，可附加 `document_id` 限定范围，返回来源片段 |
| `DELETE /api/v1/documents/{id}` | 删除自己的文档及其分块 |

支持 UTF-8 的 `.md` / `.txt`，单文件最多 256 KiB、120000 字符、256 个分块，每人最多 20 份文档。上传请求体另有 320 KiB 上限，包含 multipart 元数据，也检查没有 Content-Length 的流式请求。来源行号对应换行规范化后的原文。检索请求可用 `method: "vector"` 或 `"hybrid"`；默认 `"bm25"`。向量模式当前最多处理检索范围内 256 个片段，超过时需缩小范围；混合模式会回退并返回 `warning`。

BM25 是本阶段的关键词检索基线，中文使用相邻双字片段分词，英文按词匹配。它不等同于向量语义检索：同义表达、缩写和复杂语义可能漏检。原文只作为参考数据，不执行其中的命令；生成回答的引用列表表示本次检索来源，回答忠实度仍需要真实模型评估。

示例文档位于 `examples/knowledge/`，检索评估用例位于 `examples/retrieval-eval.json`。详细范围、实测结果和学习练习见 [第二阶段说明](docs/PHASE2.md)。

检索评估已升级为片段级：使用 5 份合成资料、30 个独立标注问题，按文件名、证据行号与原文引用共同判定。当前首条命中率为 72.7%，前五条平均证据召回率为 88.6%，8 个无答案问题中 4 个仍返回片段。不同表述和长文的失败样例完整保留；这是小型离线诊断集，不能作为真实模型或生产检索质量的结论。见 [最新评估报告](docs/RETRIEVAL_EVAL_REPORT.md) 和 [评估使用说明](docs/RETRIEVAL_EVAL.md)。

## 验证

后端测试需要运行中的本地 PostgreSQL。使用项目的隔离脚本新建临时数据库、执行迁移和测试，结束后只移除本次创建的测试数据库，保留应用账号和文档。模板的原始 pytest fixture 会清理它所连接的数据库，因此不要直接对应用数据库运行完整测试。

```bash
cd backend
uv run --frozen python scripts/test-local.py
```

片段级检索评估可单独运行，不需要 `.env`、模型密钥、数据库或网络连接。从 `backend/` 执行：

```bash
uv run --frozen python scripts/evaluate-retrieval.py
```

生成 `.local/retrieval-eval.json` 和 `docs/RETRIEVAL_EVAL_REPORT.md`。默认成功生成报告返回 0，即使检索样例存在失败；加 `--strict` 要求每个样例都满足首条正确、完整召回或无答案返回空结果，当前基线会返回 1。无效数据返回 2，区分评分结果与脚本故障。

前端构建与独立聊天 UI 模拟测试：

```bash
cd frontend
bun run build
PLAYWRIGHT_BROWSER_CHANNEL=chrome bunx --no-install playwright test -c playwright.knowledge.config.ts
```

独立 UI 测试使用模拟接口，只证明页面行为。真实登录、PostgreSQL 联调与真实 DeepSeek 调用须单独验证；缺少密钥时不会把模拟答案计作真实模型成果。

本地 `.env` 已排除于 Git 与根目录、backend、frontend 的容器构建上下文。只提交空密钥配置示例 `.env.example`。实际结果见 [验收记录](docs/VERIFICATION.md)。

## 学习与后续

阅读 [第一阶段学习说明](docs/LEARNING.md)、[第二阶段说明](docs/PHASE2.md) 与 [第三阶段整合记录](docs/ZEBAO_RAG_INTEGRATION.md)，理解请求链和文档解析、分块、检索的区别。后续评估混合检索与真实模型回答，再把多模态、受控工具和有状态 Agent 接入网页。

本机 Docker 已有另一项服务使用 IPv6 的 8000 端口，因此本项目明确使用 IPv4 API 地址 `http://127.0.0.1:8000`，前端仍为 `http://localhost:5173`。
