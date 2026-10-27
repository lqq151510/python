# DevAssist / zebao-rag 协作交接

## 2026-10-16 开心（Codex）整合

泽宝确认以 DevAssist 为主项目，将 Trae 开发的 zebao-rag 接入，并通过泽宝转发协作消息。

主项目：`devassist`。导入模块：`services/zebao-rag`。原 `../zebao-rag` 保留原样，
整合后新增功能以主项目中的模块为维护位置，避免在两份源码里各自修改同一功能。
没有执行 Git 提交、推送、发布或原目录清理。

### 已实现的整合边界

1. 导入 11 个 Python 源模块、原依赖锁和 Python 版本配置，SHA256 来源记录在 `services/zebao-rag/UPSTREAM.json`。
2. 后端文档检索和知识库问答支持 `bm25`、`vector`、`hybrid`。默认 BM25；混合检索使用 RRF 融合排名。
3. worker 是独立 Python 3.12 进程；DevAssist 保留 Python 3.14。通过本机 HTTP 和随机令牌调用，禁用代理与重定向。
4. 后端先在 SQL 中按登录用户和指定文档过滤，再提供候选片段；返回 ID 必须属于当前候选。
5. worker 使用 Milvus 持久保存应用向量，Chroma 临时集合用于对照；不打开原个人库，不扩大查询范围。Redis 缓存只保留命中 ID 与分数并设有效期。
6. 网页支持三种检索方式，展示实际使用方式与降级信息，保留来源行号和文档跳转。

### 尚未接入网页的功能

多模态图片上传、以图搜图、Ollama 自动工具循环、会话落盘和摘要压缩保留在独立 CLI 模块中。
它们尚未改造成 DevAssist 的多用户接口，网页聊天仍是单轮。
Trae 回执中的检索 24 条、生成 8 条等数字属于原项目的历史结果，本次不沿用为整合验收结论。

### 下一步配合方式

Trae 可先只读审阅 `services/zebao-rag` 和 `docs/ZEBAO_RAG_INTEGRATION.md`。
后续分工应指定具体文件；若要修改模型编码与 Agent，请基于主项目的导入副本开展，
保留 `UPSTREAM.json` 作为导入前版本证据。原个人语料迁移需要另行明确范围。

验证详情与剩余限制写在 `docs/ZEBAO_RAG_INTEGRATION.md`。


## 2026-10-16 五库学习功能追加（泽宝确认）

- 用户明确要求 PostgreSQL、MySQL、Redis、Milvus、Chroma 都有真实用途，并保持初学者能理解、能练习的结构。
- PostgreSQL 保留用户与文档；MySQL 新增 `devassist_learning_records`；Redis 使用 1 号逻辑库、独立 `devassist:` 前缀和 180 秒 TTL；Milvus 使用 `devassist_text_v1` 保存向量与 ID；Chroma 采用临时集合做对照。
- 新增 `/learning` 学习实验页和 `/api/v1/learning` 接口，支持学习记录新增/列表/删除、两库真实检索对照、缓存计数。实验结果写入 MySQL，登录用户只能访问自己的记录。
- 真实联调已完成：临时 PostgreSQL + 临时 MySQL 数据库、实际 Redis 缓存、实际 Milvus 和 Chroma、本机 Qwen 模型。后端 158 passed；UI 模拟接口回归 26 passed；生产构建通过。
- 兼容修复：现有 Milvus 为 2.4.15，模型环境使用匹配的 pymilvus 2.4 系列和 Setuptools<81；仅为本机 gRPC 设置代理绕过，没有修改系统代理。
- 初学者练习与边界详见 `docs/LEARNING_DATABASES.md`。未声称已经掌握所有技术，也未将五库学习实现当作生产架构。
- 待完善：跨库账号删除联动、历史向量物理清理、系统检索评估、网页图片和持久化多轮会话。真实生成模型本轮未验。
- 文件范围增加：`learning_store.py`、`retrieval_cache.py`、learning API/前端页面、`milvus_store.py`、依赖锁与启动脚本。原 zebao-rag 的 14 个导入来源文件仍保留。
- 已实现、已验证；未提交、未推送、未发布。Trae 请优先阅读本段与五库练习，再登记需要修改的具体文件。


最终启动验收：`bash scripts/devassist-dev.sh --with-rag` 已完整跑通；实际 HTTP 的健康检查、登录、文档列表、MySQL 学习记录列表返回 200，Redis 状态可用。使用 `http://localhost:5173/learning`，与后端允许的浏览器来源一致。最新后端回归为 158 passed（12.32 秒），浏览器为 26 passed；真实生成模型仍未验。脱敏验收摘要在 `.local/zebao-rag/final-validation.json`。
