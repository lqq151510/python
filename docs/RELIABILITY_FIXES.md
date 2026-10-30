# DevAssist 可靠性修复记录

日期：2026-10-23。范围为现有项目的本地修复，没有修改真实凭据、迁移数据库或执行发布。

## 2026-10-30：Milvus 维度不匹配保护

当现有 Milvus 集合的向量维度与当前模型不同时，worker 抛出
`MilvusDimensionMismatch` 并返回 `503 ranking_failed`，不删除、重建或写入该集合。
后端纯向量检索返回不可用；混合检索按既有逻辑降级为 BM25 并附带警告。
排查时先核对模型、adapter 和 worker 的集合配置；如需更换集合或重建索引，
先确认集合归属、备份与重建方案，并另行授权执行。本次未连接真实 Milvus、
更改远端集合或执行重建。该保护只覆盖维度不同，不能识别同维度模型的变更。

## 行为改动

- 账号退出与登录切换取消并清理旧查询；身份查询接入取消信号，新身份获取成功后再进入业务页面。旧写请求迟到的鉴权错误只有仍属于当前会话时才触发退出；旧账号成功回调持有的退出函数也会检查会话归属。
- 代码文件上传和候选改动应用成功后，刷新相应文件的内容查询；知识库携带问题、文档或检索方式跳转时优先进入 RAG 模式。
- 批量写入在提交前准备全部新文件和原始备份。失败后逆序恢复，撤回新建文件；恢复失败返回 `workspace_rollback_failed`，并保留 `.<原文件名>.rollback-<随机标识>` 备份。
- 同进程的工作区上传与应用改动串行执行；代码生成所需的文件读取移入线程，避免阻塞异步事件循环。
- 向量候选总上限与上传配额对齐为 5120，每批最多 256；校验每批返回 ID，合并全局前 K 条结果，所有批次共享超时预算。失败不会缓存部分结果，混合检索沿用原有降级行为。
- AIGC 在恢复任务前取得 PostgreSQL 会话独占锁，线程停止后才释放；第二个执行器启动失败。容器默认一个 API worker。
- 保留代码配置接口的鉴权依赖，并修正未使用参数命名；同步 README 中图片库、聊天历史和多轮 Agent 的实现边界。

## 后端验证

在仓库根目录，向测试进程注入公开的占位配置，不读取真实凭据：

```bash
PROJECT_NAME=DevAssist SECRET_KEY=offline-test-only \
FIRST_SUPERUSER=offline@example.com FIRST_SUPERUSER_PASSWORD=offline-test-only \
DATABASE_URL=postgresql://postgres:offline-only@localhost:5432/app \
PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 AIGC_ENABLED=false \
.venv/bin/python -m pytest \
  --noconftest -p no:cacheprovider \
  backend/tests/test_aigc_startup.py \
  backend/tests/test_code_workspace_offline.py \
  backend/tests/test_semantic_batches.py \
  backend/tests/test_retrieval_eval.py \
  backend/tests/test_aigc.py -q
```

结果：**88 项通过**，其中新增启动保护 9 项、工作区故障注入 10 项、向量分批 20 项。
`--noconftest` 用于避开会操作数据库的自动夹具；文件写入仅发生在测试临时目录。
Ruff 对 `backend/app`、`backend/tests` 的检查通过。

## 前端与配置验证

- TypeScript 全前端无产物类型检查通过。
- 系统 Chrome + 模拟 API 的聊天、知识库、学习模块回归 **34 项全部通过**，其中新增 7 项，覆盖账号切换、旧文档响应、旧写请求迟到 401、旧退出回调、当前会话 401、上传与应用后的预览更新、RAG 深链。
- 三个既有测试案例的断言与当前契约同步：历史恢复检查模拟回答，问答请求体必须包含符合 UUID 格式的 `client_request_id`；没有降低这些断言的严格程度。
- 定向 Biome 检查、`git diff --check`、相关 Shell 语法和主 Compose 配置解析通过。`chat.tsx` 原有 `role="group"` 无障碍告警仍保留，不宣称全项目 Biome 零告警。
- 浏览器测试使用本任务启动的临时 Vite 服务；测试后已停止，端口 5175 无监听。浏览器回归不等于真实数据库或模型联调。

固定 BM25 离线检索门槛通过：30 个样例中 20 个满足完整判定，Top-1 为 72.73%，
Recall@5 为 88.64%，MRR@5 为 0.8106，无答案正确率为 50%。这项修复没有改变该检索质量基线。

## 验证边界与恢复

- 上述首轮定向测试没有调用真实数据库或模型；后续真实 PostgreSQL、HTTP 和本地模型结果见下方追加验收。真实嵌入模型、Redis/Milvus/Chroma、ComfyUI 和锁断连实验仍未执行。
- 工作区回滚适用于进程正常运行期间的可捕获写入异常，不提供跨进程、外部文件修改或进程突然终止时的事务保障。失败可能留下空目录；恢复失败的备份需要人工检查后恢复。
- AIGC 只支持单实例、单 worker，所有实例应连接同一应用数据库。正常关闭会等待后台线程退出；数据库锁连接意外断开后的严格独占和 ComfyUI 恰好一次执行不在保证范围内，详见 [AIGC 部署约束](AIGC.md)。
- 没有对原应用数据库执行 schema 或持久配置迁移。代码回退应仅逆向本次文件改动并重启对应进程，保留已有工作区和恢复备份；不要全局清理或覆盖既有未提交文件。

## 2026-10-23 追加：真实数据库与模型联调

新增可重复执行的隔离验收：

```bash
# Docker Engine 已运行，postgres:16-alpine 已缓存，指定 Ollama 模型已安装。
.venv/bin/python scripts/verify-isolated.py \
  --model qwen2.5-1.5b-instruct:latest
```

该编排器使用独立唯一容器、随机回环端口与 tmpfs 数据目录，从空临时工作目录启动子进程，
仅注入本轮生成的配置，不读取项目 `.env`。不拉取镜像、不下载模型、不启动原应用数据库，
不修改原数据库和数据卷。仅需迁移与后端回归时可加 `--skip-runtime`。

最新执行编号：`8f0f07ba13f2`，脱敏证据保存在 `.local/isolated-validation/8f0f07ba13f2/`：

- 空库完成全部 Alembic 迁移，head 为 `e8c1a4d72b90`。
- 完整后端测试 **260 passed、2 skipped**；跳过的是显式选择开启的真实多库/向量测试。
- 有一条现有 Starlette TestClient 的 httpx 弃用告警，没有在本次修改依赖。
- `backend/scripts/verify-runtime.py` 完成 **22 次真实 HTTP 请求 + 1 项真实双引擎数据库锁检查**。
- API 业务写入前，先在临时库内建立随机身份，仅通过登录和读取身份确认 API 使用同一数据库，再开始注册、上传等步骤。身份不匹配时立即中止。
- 容器创建超时后仍会查找本次唯一标签；清理先核对标签，再使用稳定容器 ID 停止和删除。各项清理独立处理，失败仍尝试写出总结。新增 8 项故障/身份保护离线测试通过。
- 两合成账号完成注册、登录、文档上传和 BM25 检索；其他账号访问文档、指定文档检索、历史或工作区文件均被拒绝。
- 使用本地 `qwen2.5-1.5b-instruct:latest` 完成一次真实 RAG 生成并落库；同请求 ID 重放不增加历史，内容变化返回 409。
- 代码工作区上传、应用修改与读回内容一致。
- 双物理数据库会话验证：A 持锁时 B 被拒绝，A 释放后 B 成功；这不包含断连或 ComfyUI 执行验证。
- 唯一合成质量检查包含预期事实 `ASTER-7319`，返回一个来源；单样例成功不能代表整体引用或拒答质量。
- 临时 API 已停止、临时容器和临时目录已移除；`learning-mysql` 和 `devassist-db-1` 仍保持原停止状态。
  Docker Desktop 在本轮启动后保持运行。

同时修正了两处邮件测试的 mock（模拟替代）目标：路由已经导入的 `send_email` 别名应在
`app.api.routes.login` 和 `app.api.routes.users` 替换，并断言收件人。测试不再依赖真实 SMTP。

首次真实执行 `eabc729f69b8` 的 252 项测试与 21 项运行时检查也已通过；上述最新记录是在
复核补齐异常清理和 API/数据库身份保护之后重新执行的结果。两次报告均保留在忽略目录。

这些结果补齐 PostgreSQL/API/真实本地模型的验收；真实向量检索、多库联调、ComfyUI、
数据库断连故障及浏览器直接连接真实后端仍需分别验证。
