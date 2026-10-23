# 请求追踪与故障检查

`APIRequestIDMiddleware` 为 `/api/v1` 及其子路径创建服务端请求编号，写入 `request.state.request_id`、请求上下文与响应 `X-Request-ID`。编号覆盖成功、鉴权失败、校验错误与普通未捕获异常；中间件在返回或取消时复原请求上下文。非 API 静态页面不在此范围内。

请求日志的 message 是 JSON，外层日志框架可能添加前缀。`event=api_request` 包括 `request_id`、`method`、`route`、`elapsed_ms`、`status_code`、`error_code` 和 `error_type`。`route` 采用路由模板，未知路径记为 `<unmatched>`；日志不包含实际文件路径参数、查询参数、请求体、响应正文或认证标头。耗时测量到响应头准备完成，不能表示未来流式响应的完整传输耗时。

普通未捕获异常返回脱敏的 `500/internal_error`。日志仅保留异常类型，异常原文与堆栈不进入这条请求事件。模型的已分类错误保留现有响应和 `chat_error_code`。

`vector_rank` 的 `operation_completed` 日志包含操作名、关联的请求编号、`succeeded/failed`、耗时和异常类型。原始返回值、异常传播和缓存行为保持原逻辑；直接在 HTTP 请求以外调用时请求编号为空。此处记录同步检索操作的终态，不表示已经实现持久化后台队列。

## 查看与汇总

在日志系统中按 `request_id` 关联 `api_request`、`operation_completed` 和既有模型用量日志。从项目根目录汇总已捕获的日志：

```bash
.venv/bin/python backend/scripts/summarize-requests.py < /path/to/backend.log
```

汇总输出包括请求总数、4xx 数、5xx 数、各操作成功/失败次数、请求平均/最大耗时及无效事件数量。4xx 与 5xx 分别统计。无关日志不进入统计，损坏的监控事件计入 `invalid_events`；汇总不保留用户数据或请求编号，不创建远端监控服务。

## 验证范围

新增的隔离 ASGI 检查覆盖非聊天接口的编号、同步线程中的上下文、路由模板脱敏、401/404/500、安全 CORS、检索操作成功/失败终态、返回值和异常传播，以及日志汇总。它们不连接数据库或模型供应商。

```bash
cd backend
../.venv/bin/python -m pytest --confcutdir=tests/observability tests/observability/test_requests.py -q
```

`--confcutdir` 将这组纯中间件检查与原有数据库 autouse 夹具隔离；普通业务回归继续使用项目已有的独立临时数据库流程。

本轮还在独立端口启动了真实 Uvicorn 应用，只给该临时进程注入一条诊断路由：一次受控异常返回 500，随后健康检查返回 200；异常操作事件与 500 请求编号一致，日志不包含异常原文。验收结束后该进程已停止，诊断路由不在项目应用代码中。没有对用户数据库、真实模型或当前开发服务注入故障。

Sentry 是否收到外部事件、真实向量服务的延迟，以及公网部署告警，需要在相应环境单独验收。
