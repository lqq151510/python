# DevAssist 本地运行

## PostgreSQL 与迁移

Docker Desktop 启动后，使用独立的 devassist Compose 项目运行 PostgreSQL。
数据库结构通过 Alembic 迁移管理，不依靠接口启动时临时创建表。
回归测试在单独创建的临时数据库中执行，完成后移除该测试数据库。
应用数据库和上传的文档保留。

## 本地地址与 IPv6

前端地址为 http://localhost:5173，后端使用 http://127.0.0.1:8000。
Docker 中其他服务可能使用 IPv6 的 8000 端口，因此后端明确使用 IPv4 地址。
OpenAPI 描述在 /api/v1/openapi.json，接口文档在 /docs。
