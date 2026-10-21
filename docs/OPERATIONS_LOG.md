# DevAssist 操作记录

## 2026-10-17：为问答历史与运维闭环准备交付

- 代码变更：新增按用户隔离的聊天历史、Alembic 迁移、备份/恢复脚本、CI 检索评测门禁和 Playwright 历史主流程。
- 本机 Docker Engine：**不可用**。`docker info` 报告无法连接 `unix:///Users/liuyongze/.docker/run/docker.sock`。
- Compose 配置、容器部署、PostgreSQL 备份/恢复、Alembic 回滚：本记录生成时尚未执行；因此没有数据库或运行中服务被修改。
- 离线检索评估 CLI：20 项测试通过，固定非回退门槛通过；严格全样例模式仍报告 20/30，属于已记录的诊断基线。
- 前端生产构建、Python 编译检查、Shell 脚本语法检查和 `git diff --check`：通过。
- 后端接口测试因本机 PostgreSQL `localhost:5432` 不可连接而无法启动测试夹具；完整数据库集成与 Playwright 浏览器主流程需在 Docker Engine 可用后执行。
- 后续演练结果应追加 Git SHA、Alembic revision、备份文件校验、健康检查、恢复前后记录数和回滚后页面/API 检查。
