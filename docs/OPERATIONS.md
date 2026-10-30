# DevAssist 部署、数据库恢复与回滚

本操作手册面向本机 Docker Compose 环境。执行前确认当前目录是 DevAssist 仓库、`.env` 已配置且不会覆盖其他 Compose 项目。数据库恢复会替换 `app` 数据库；务必使用本项目备份并核对目标。

## 干净环境部署

1. 安装 Docker Desktop 并启动 Docker Engine。
2. 按根目录 `.env.example` 建立 `.env`，填写必需项和密钥；不要把 `.env` 加入 Git。
3. 检查配置并构建、启动服务：

   ```bash
   docker compose config -q
   docker compose --project-name devassist up -d --build
   ```

4. 核对迁移、服务状态和健康接口：

   ```bash
   docker compose --project-name devassist exec -T backend alembic current
   docker compose --project-name devassist ps
   curl --fail --silent http://localhost:8000/api/v1/utils/health-check/
   ```

5. 记录 Git 提交、Alembic revision、镜像标识、启动时间和健康检查结果到 [OPERATIONS_LOG.md](OPERATIONS_LOG.md)。

## 备份与恢复演练

备份脚本生成 PostgreSQL custom-format 归档，先写入同目录的私有临时文件，用 `pg_restore --list` 检查归档目录，再将完整归档解析为 SQL 并写入 `/dev/null`。校验通过后，使用随机文件名和 Python 3 的 `os.link` 原子发布，不覆盖已有路径；备份目录须支持硬链接。文件存放在被 Git 忽略的 `.local/devassist-backups/`。完整解析只验证归档可读取，不能证明它能在目标数据库成功恢复。

```bash
scripts/backup-devassist-db.sh
docker compose --project-name devassist exec -T db pg_restore --list \
  < .local/devassist-backups/<备份文件名>
```

恢复前会将目标归档复制到仅本次使用的私有快照，再对快照做目录检查和完整解析；任一步骤失败时不会创建恢复前备份、停止 backend 或重建数据库。预检与最终恢复使用同一快照，执行结束后清理该快照；请预留至少一份待恢复归档大小的临时磁盘空间。校验通过后，脚本会自动给当前数据库再做一份恢复前备份；随后停止 backend、重建 `app` 数据库并恢复归档。恢复源必须位于备份目录内，物理路径不得通过父目录符号链接逃逸，文件本身也不能是符号链接。归档解析通过并不保证 SQL 能在目标数据库成功执行。只有明确确认后才会运行：

```bash
DEVASSIST_RESTORE_CONFIRM=RESTORE_DEVASSIST_DATABASE \
  scripts/restore-devassist-db.sh .local/devassist-backups/<备份文件名>
```

恢复后检查 `alembic current`、健康接口、账号登录、文档数量、一次带引用的问答和日志。若恢复失败，脚本会让 backend 保持停止，避免服务继续写入不完整数据库；使用脚本刚生成的恢复前备份修复后再启动。

## 版本回滚

部署前记录当前 Git SHA、Alembic revision，并完成数据库备份。优先回滚应用镜像/代码到上一个已验证版本；如果数据库 schema 与旧版本兼容，不需要回退 schema。可从单独 worktree 构建旧版本，Compose 项目名仍需指定为 `devassist`，并使用原部署目录的 `.env`：

```bash
git worktree add --detach ../devassist-rollback <上一个已验证的提交 SHA>
cd ../devassist-rollback
docker compose --project-name devassist --env-file ../devassist/.env up -d --build backend
```

只有旧应用确实无法兼容新 schema 时，才在备份后把 Alembic 回退到事先记录的 revision：

```bash
docker compose --project-name devassist stop backend
docker compose --project-name devassist run --rm backend alembic downgrade <目标 revision>
docker compose --project-name devassist --env-file .env up -d --build backend
```

数据库降级可能删除新版本写入的数据（例如聊天历史表）。不要在未备份、未确认目标 revision 或仍有写入流量时执行。回滚后再次检查迁移版本、健康接口、文档引用和日志，并把结果写入操作记录。

## 固定 RAG 检索评估

CI 使用 `examples/retrieval-eval-gate.json` 对合成诊断集执行非回退检查，包含通过数、Top-1、Recall@5、MRR@5、无答案准确率和误报率。基线已经记录了已知失败样例，因此这项检查不要求每个问题都通过；`--strict` 仍可在本地用于定位全部失败样例。该数据集只衡量当前离线 BM25 检索器，不代表第三方模型、生成答案质量或生产数据效果。

```bash
cd backend
uv run python scripts/evaluate-retrieval.py \
  --gate ../examples/retrieval-eval-gate.json \
  --json-output /tmp/devassist-retrieval-eval.json \
  --report /tmp/devassist-retrieval-eval.md
```

## 环境限制

当前开发机没有可连接的 Docker Engine 时，只能完成 Compose 静态配置检查和脚本语法检查；这不能证明容器部署、备份恢复或版本回滚已经成功。操作记录必须分别标注“配置检查通过”和“运行演练通过”。
