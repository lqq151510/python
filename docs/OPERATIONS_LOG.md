# DevAssist 操作记录

## 2026-10-17：为问答历史与运维闭环准备交付

- 代码变更：新增按用户隔离的聊天历史、Alembic 迁移、备份/恢复脚本、CI 检索评测门禁和 Playwright 历史主流程。
- 本机 Docker Engine：**不可用**。`docker info` 报告无法连接 `unix:///Users/liuyongze/.docker/run/docker.sock`。
- Compose 配置、容器部署、PostgreSQL 备份/恢复、Alembic 回滚：本记录生成时尚未执行；因此没有数据库或运行中服务被修改。
- 离线检索评估 CLI：20 项测试通过，固定非回退门槛通过；严格全样例模式仍报告 20/30，属于已记录的诊断基线。
- 前端生产构建、Python 编译检查、Shell 脚本语法检查和 `git diff --check`：通过。
- 后端接口测试因本机 PostgreSQL `localhost:5432` 不可连接而无法启动测试夹具；完整数据库集成与 Playwright 浏览器主流程需在 Docker Engine 可用后执行。
- 后续演练结果应追加 Git SHA、Alembic revision、备份文件校验、健康检查、恢复前后记录数和回滚后页面/API 检查。

## 2026-10-23：隔离 PostgreSQL、真实 HTTP 与本地模型验收

- Docker Desktop 启动成功；使用已有 `postgres:16-alpine` 镜像，不拉取镜像。
- 建立唯一临时容器 `devassist-validation-eabc729f69b8`，数据仅在 tmpfs，随机回环端口，数据库为本轮专用。未启动或修改原有数据库容器和卷。
- 从空临时目录运行迁移与测试，不读取项目 `.env`。迁移 head `e8c1a4d72b90`，完整后端 `252 passed, 2 skipped, 1 warning`。
- 新运行时脚本完成 20 次真实 HTTP 与一项真实双引擎锁检查；本地 Ollama 模型 `qwen2.5-1.5b-instruct:latest` 实际生成，回答与来源落库，幂等与账号隔离检查通过。
- 仅一个合成问题检查到预期事实，不能作为整体模型质量结论。未验证真实向量服务、ComfyUI、浏览器接真实后端或数据库断连。
- `.local/isolated-validation/eabc729f69b8/summary.json`、`runtime.json`、`pytest.log`、`migrations.log` 保存脱敏结果，不进入 Git。
- 收尾核验：临时 API 停止、临时容器移除；原 `learning-mysql` 与 `devassist-db-1` 保持停止。Docker Desktop 保持运行。
- 本轮没有操作应用数据库，没有执行备份恢复、版本发布或回滚演练；代码仍为本地未提交改动。
- 复核追加：验证脚本补齐容器创建/停止异常清理、API 与临时数据库随机身份绑定，新增 8 项离线保护测试。
  最新执行 `8f0f07ba13f2`：**260 passed、2 skipped、1 warning**，**23 项运行时检查通过**，
  对应 `.local/isolated-validation/8f0f07ba13f2/summary.json` 与 `runtime.json`。
  临时 API、容器、目录均已清理，原数据库容器仍停止。
