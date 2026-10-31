# DevAssist 本机发布候选

日期：2026-10-31。本指南面向本机展示与单机发布准备，公网部署、域名和 TLS 不在本轮验收范围。
本轮使用独立数据库及合成资料，不读取开发库，不加载大模型。

## 三条命令启动

在仓库根目录运行，需要 Docker Engine 已启动，以及 Python 3：

```bash
python3 scripts/setup-release.py
bash scripts/devassist-release.sh --env-file .env.release check
bash scripts/devassist-release.sh --env-file .env.release up
```

打开 <http://127.0.0.1:8088>。账号为 `admin@devassist.example.com`，随机密码在本地
`.env.release` 的 `FIRST_SUPERUSER_PASSWORD` 中，用本地编辑器查看。
配置生成器以 `0600` 权限创建文件，遇到已有文件或符号链接时保留原文件；不读取或覆盖开发配置 `.env`。
首次构建需要下载基础镜像和锁文件中的依赖，后续构建复用缓存。

入口脚本显式使用 `compose.release.yml`，不加载 `compose.override.yml`。
顺序为：构建镜像 → 数据库健康 → 停止旧后端 → 迁移与初始化管理员 → 后端健康。
中途失败立即停止；迁移失败后保持后端停止，不能把此时的服务视为已发布。

所有应用配置来自传入的文件，脚本会清除同名的 shell 环境变量，避免其他项目的密码、端口或模型密钥覆盖它。
Docker 与代理环境保留。改端口请编辑 `RELEASE_PORT`，改版本标签请编辑 `RELEASE_TAG`，然后再次运行 `up`。
`POSTGRES_PASSWORD` 必须为 URL-safe 值，生成器默认使用随机十六进制；数据库建立后不要直接改密码字段。

## 三分钟展示流程

1. 登录后进入一体化工作台。全空账号显示“先添加一份资料”，可直接上传资料或新建速记。
2. 打开知识库，上传仓库中的 `examples/knowledge/async.md`。这是公开合成学习资料，不含私人内容。
3. 使用关键词 `async await` 检索；打开命中资料，核对原文和片段位置。
4. 进入问答页并点击“只检索原文”，查看来源面板。没有配置模型密钥也能完成这一步。
5. 回到工作台，搜索刚上传的资料，将其加入上下文；新建一条速记，刷新后确认资料和速记仍在。
6. 在手机宽度检查搜索、主题、返回按钮，以及知识源面板展开和关闭。

文档检索使用 BM25 关键词算法。检索命中不保证生成答案正确，也不等同于语义向量检索。
回答生成、编程生成需要在发布配置中另行填写模型服务信息；不要把 API Key 放进前端变量。
容器中的 `127.0.0.1` 指向容器自身，本机 Ollama 地址不能直接照搬开发环境配置。

## 数据与服务范围

| 项目 | 发布入口行为 |
|---|---|
| 应用入口 | 默认仅 `127.0.0.1:8088`，生产构建、单 API worker、同源请求 |
| PostgreSQL | 独立 Compose 网络与持久卷，不映射宿主端口 |
| 文档、笔记、账号、聊天历史 | 保存在本发布项目数据库，不共享开发库 |
| 编程沙箱 | 独立持久卷，仅覆盖应用管理的沙箱 |
| AIGC、向量服务、学习 MySQL、缓存 | 本机精简发布配置固定关闭，不包含这些服务的发布验收 |
| 模型与 SMTP | 默认未配置；模型成功回答和邮件投递需要独立联调 |
| 辅助面板 | 不启动 Adminer、Traefik dashboard、Mailpit 或开发热更新 |

默认项目名 `devassist-release`，其镜像名为 `devassist-release:<RELEASE_TAG>`。
需要并行演示时，使用 `--project-name devassist-release-<名称>`，并准备不同的端口和独立配置文件。
脚本拒绝使用现有开发项目名 `devassist`。项目名不同会隔离容器、网络、镜像名和持久卷。

## 验证命令与证据边界

```bash
# 后端：新建并清理一次性数据库；不加载模型
.venv/bin/python scripts/verify-isolated.py --skip-runtime

# 发布命令的顺序、失败即停止与配置隔离
.venv/bin/python -m unittest discover -s scripts/tests -p 'test_release_entry.py' -v

# 前端：独立本机端口，所有 API 为模拟数据
(cd frontend && bun run typecheck && bun run build)
(cd frontend && PLAYWRIGHT_BROWSER_CHANNEL=chrome bun run test:mock)
```

`test:mock` 包含认证页、问答、知识库、学习页、Agent、工作台与图片重新嵌入的模拟测试。
它不会复用现有开发服务器或登录状态；未模拟的 API 请求会失败，不会落到真实后端。
默认使用端口 4189；端口被占用时会直接失败。没有 Chrome 的环境可安装项目匹配的 Playwright Chromium 后省略 channel。

本轮工作基于 `2e97784`，未新增数据库迁移。验证结果分开记录：

| 验证项目 | 2026-10-31 本轮结果 |
|---|---|
| 一次性 PostgreSQL 后端回归 | 412 passed、2 skipped、5 subtests passed；保留 1 条既有 Starlette/httpx 警告 |
| 数据库护栏离线测试 | 41 passed |
| 前端模拟 API 浏览器测试 | 98 passed，含 375px / 768px 工作台和认证页面 |
| 前端生产构建与完整类型检查 | 通过；类型检查覆盖 src、tests 和全部 Playwright 配置 |
| 修改范围内 Biome | 24 文件通过；4 条减少动画样式的既有警告 |
| 发布脚本测试 | 8 passed，包含环境优先级、失败即停止和保留数据 |
| 配置生成器独立临时目录检查 | 6 项通过，含权限、保留原配置和符号链接 |
| 最终生产镜像 | Linux ARM64 构建成功；前端冻结安装 279 包，缓存复建安装约 0.75 秒 |
| 新数据库启动 | 迁移和管理员初始化成功；应用与数据库健康；版本为 `df42e8a190cb`，与源码 head 一致 |
| 真实 HTTP | 文档上传去重、范围检索、笔记/沙箱保存、双账号隔离、缺模型错误与请求编号通过 |
| 真实浏览器 | 登录、上传、检索原文、上下文引用、速记保存和刷新通过；1440px / 375px，0 控制台错误、0 失败请求 |
| 停止后再启动 | 文档、笔记、沙箱文件保留；迁移及初始化可重复执行 |
| 镜像与网络边界 | 运行平台 manifest 与构建一致；应用仅回环端口，数据库无宿主端口，无源码 bind mount，应用目录无 `.env`/登录状态文件 |
| 源码候选密钥扫描 | 通过；源码包排除环境配置、登录状态、本地资料与构建缓存 |

详细本机日志在 `.local/final-independent-verification.json`、`.local/final-test-mock.log`，
后端证据在 `.local/isolated-validation/dd24e36c04ae/`；这些被 Git 忽略，不包含在源代码交付中。
镜像与真实业务证据在 `.local/release-acceptance-2703af2d0f/`：`container.json`、`http.json`、
`http-after-restart.json` 和 `browser.json`。运行平台 manifest 为
`sha256:a169286214f1752e05edf094a616b157069b55cb6e77d6401247f71c53cde3f4`。
验证镜像是否一致时比较相同平台的 manifest，不能直接把 OCI 索引摘要与平台镜像摘要混比。

页面 HTTP 冒烟检查须携带 `Accept: text/html`；FastAPI 默认只对页面导航请求回退前端入口。
API 与资源检查保留各自的 Accept 类型。实际浏览器验证未模拟任何 API，也未调用真实模型。
首次依赖下载有过停滞，最终保留锁文件并将 Bun 下载并发限制为 16，增加 BuildKit 缓存；
诊断用 verbose 已移除。Linux AMD64、云端主机和其他浏览器尚未验收。

## 停止、升级与回滚

```bash
bash scripts/devassist-release.sh --env-file .env.release status
bash scripts/devassist-release.sh --env-file .env.release stop
```

`stop` 保留容器和数据卷，不删除资料；再次 `up` 会沿用原有数据。
入口不提供删卷、数据库恢复或破坏性清理命令。

本轮改动没有新增数据库迁移。升级前仍应记录当前镜像标识、Git 版本、Alembic revision，并备份对应发布数据库。
需要回退应用时使用之前保存的源码/镜像，确认 schema 兼容后启动；不要用 `reset --hard` 或删除数据卷回滚。
现有 `backup-devassist-db.sh` / `restore-devassist-db.sh` 面向开发项目 `devassist`，不能直接用于这里的发布项目。
生产备份恢复、SMTP、真实模型服务以及公网 TLS 需要针对目标环境另做验收。

## 状态口径

“已实现”指工作区代码已完成，“已验证”只覆盖表述中的本机环境和测试范围。
“已提交”“已推送”“已发布到公网”分别需要 Git 提交、远端核对和目标部署证据。
本机镜像通过不代表云端服务已经发布，也不证明当前模型的回答质量达到产品要求。
