# 第一阶段验收记录

这是第一阶段的历史记录；当前文档知识库、最新测试数量和隔离测试方式见 [第二阶段说明](PHASE2.md)。

验证日期：2026-10-07。本文件只记录已经执行的检查，不把模拟模型答案计作真实模型调用。

## 当前结论

本轮按照泽宝明确选择的“暂时只完成本地与模拟验收”交付，这部分已经通过。已实现并验证登录、聊天页面、认证聊天接口、模型错误分类、总时间预算、有限重试与配置保护，真实 PostgreSQL 和浏览器流程已联通。根目录 `.env` 的 `DEEPSEEK_API_KEY` 尚未填写，因此 **DeepSeek 真实成功回答留待后续验收**；完整阶段的真实模型与个人学习验收仍待完成。

## 实际结果

| 检查 | 实际结果 | 验证范围 |
|---|---|---|
| 模板起点 | HEAD 为 `e99fbaeba3b55190156ebae525ad9d481d76677e` | 保留 MIT 许可证和上游历史 |
| Python 环境 | Python 3.14.8，项目独立 uv 环境 | 未替换系统默认 Python |
| PostgreSQL | 独立 `devassist-db-1` 健康，迁移和初始账号创建成功 | 本地 PostgreSQL 16 Alpine、独立数据卷 |
| 后端测试 | **91 passed**，约 12.27 秒；其中新增聊天测试 33 项 | 真实本地数据库与模拟模型；一个 Starlette 测试客户端弃用警告 |
| 后端静态检查 | Ruff 检查及格式、mypy、ty 通过 | 本次代码及 app 类型检查 |
| 前端构建 | `bun run build` 成功，2248 个模块 | TypeScript 与生产构建 |
| 前端浏览器模拟测试 | **8 passed**，本轮恢复后复验约 10.4 秒 | 输入、加载、成功、失败、重复提交、刷新和未登录 |
| 真实 HTTP 联调 | 存活检查 200、登录 200、未登录 401、空白输入 422、聊天 503 `model_not_configured` | 本地 FastAPI、真实 PostgreSQL、未发送模型请求 |
| 真实 Chrome 联调 | 登录后进入 `/chat`，提交问题并显示缺密钥说明和请求标识 | 使用真实后端，浏览器页面运行错误为空 |
| 一键启动与重启 | `bash scripts/devassist-dev.sh` 完成依赖检查、迁移和前后端启动；停止后再启动成功 | 旧开发进程结束，PostgreSQL 数据保留 |
| 服务身份与地址 | OpenAPI 标题为 DevAssist 且存在聊天路由，接口文档和页面均返回 200 | 显式访问 IPv4 后端，检查正确的 OpenAPI 路径 |
| 总预算独立复核 | 将进程内预算设为 50ms，约 51ms 返回 504，实际模型请求 1 次 | `httpx.MockTransport` 模拟慢上游，不修改正式配置 |
| 重试独立复核 | 模拟上游 500 后 200，实际模型请求恰好 2 次 | OpenAI SDK 自动重试关闭 |
| 配置保护 | 根目录 `.env` 未被 Git 跟踪、被忽略、权限 0600 | 示例为空密钥；前后端测试登录状态被忽略 |
| 前端产物配置检查 | 扫描 `backend/app/frontend` 下 38 个构建产物，未检出本地现有凭据 | 本轮 API Key 为空，真实密钥仍未参与构建或调用 |
| 容器构建上下文 | 临时 `FROM scratch` / `COPY .` 导出的上下文无 `.env*` 文件 | 根上下文实际构建；前后端忽略规则另行核对 |
| 静态交付检查 | `git diff --check`、Shell 语法、Compose 配置解析通过 | 不打印解析后的敏感配置 |

后端覆盖空白、超长、类型错误、未登录、缺密钥、上游认证错误、429、连接失败、读取超时、5xx、异常响应、请求次数、超时上限和日志脱敏。所有聊天响应包括 401、422、405 和内部异常都检查了 `X-Request-ID`。

前端成功回答使用模拟接口；真实浏览器本轮只验证了登录和缺密钥错误。加载状态和重复点击保护已经通过受控延迟的浏览器测试验证。

## 可追踪的本地证据

证据存放在被 Git 忽略的 `.local/`，不包含密码或登录 token：

- `api-smoke.json`：健康检查、登录、未登录、无效输入和缺密钥聊天的状态。最新聊天请求标识为 `a7830574-acdf-4563-8a44-07d2abacadc3`，耗时约 3ms。
- `browser-smoke.json`：真实 Chrome 与真实后端联调，`real_model_attempted: false`，`console_errors: []`；最新请求标识为 `6522b404-dc02-4d1b-b518-62e27f2d34f8`。
- `runtime-smoke.json`：DevAssist 服务身份、健康检查、接口文档和页面地址的检查结果。
- `config-safety.json`：Git 忽略、文件权限、构建产物凭据检查与已导出的容器上下文检查结果。
- `chat-live.png`：登录后的聊天页面与缺密钥错误截图。

## 本地环境调整

模板默认 PostgreSQL 镜像下载较慢，本机改用已有 PostgreSQL 16 Alpine，并同步调整数据卷挂载目录；本阶段使用的模板迁移已实际通过。后续变更数据库大版本需要另行迁移，不能直接复用数据目录。

Docker 启动后恢复了本机既有 `markitdown-service`，其 IPv6/wildcard 监听占用 8000。DevAssist 后端和前端 API 地址明确使用 `127.0.0.1:8000`；浏览器页面仍是 `localhost:5173`。没有停止该既有服务。启动脚本的端口预检采用与 Uvicorn 一致的 `SO_REUSEADDR`，避免把可共存的监听误判为冲突。

此前请求 `/openapi.json` 返回 404，是因为模板把 OpenAPI 描述配置在 `/api/v1/openapi.json`。正确地址已实际返回 DevAssist 的接口描述；`/docs` 返回 200，无需修改后端路由。

## 尚待完成

1. 泽宝在本地 `.env` 填入有效 DeepSeek API Key，重启后端，从页面获得真实答案；记录请求标识、模型耗时和用量，并确认浏览器及日志没有密钥。
2. 泽宝亲自解释请求链路，修改输入长度或总超时后重新验证行为。

本阶段未构建完整前后端应用 Docker 镜像，未验证公网部署；没有提交、推送或发布。数据库仅验证了本地环境。下一阶段文档导入、RAG 与工具调用未开始。

## 复现命令

```bash
# 项目根目录
bash scripts/devassist-dev.sh

# backend：使用后续新增的隔离脚本，不清理应用数据库
cd backend
uv run --frozen --no-sync python scripts/test-local.py

# frontend：需要本地 Chrome 和运行中的开发页面
cd ../frontend
bun run build
PLAYWRIGHT_BROWSER_CHANNEL=chrome PLAYWRIGHT_BASE_URL=http://localhost:5173 \
  bunx --no-install playwright test -c playwright.chat.config.ts
```

以上测试必须连接本项目本地数据库。停止开发进程不会删除数据库；回滚聊天功能可依据工作区 diff 仅撤回本阶段文件。不要通过批量 reset 或删除数据卷清理工作区。
