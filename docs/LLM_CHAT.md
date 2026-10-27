# 真实模型问答验证（本地 Ollama 兼容端点）

验证日期：2026-10-20。本文件只记录本次实际执行的结果，用来补上 [第一阶段验收记录](VERIFICATION.md)「尚待完成 1」中「从页面获得真实答案」这一项。**本次用的是本机 Ollama 上的小模型，不是 DeepSeek 云端**；DeepSeek 官方接口仍需泽宝填有效 Key 后单独验证。

## 结论

- 完整链路已经用真实模型跑通：登录 → 上传 Markdown → BM25 检索 → 真实模型生成 → 返回来源片段 → 写入 `chatturn` → 历史接口可读，6 次请求全部 200。
- 模型通过 Ollama 的 OpenAI 兼容端点 `/v1/chat/completions` 接入，devassist 侧**没有改任何业务代码**，只注入环境变量。
- 1.5B 级模型能答对「答案就在片段里」的问题（如「小型检索评估用了 12 个样例」0.99 秒答对），但**引用标注和「资料不足就说不知道」不稳定**：6 个问题里出现 1 次引用写错、1 次凭空回答「支持导出 MP4」。
- 因此本次可以确认「链路通、可复现」，不能用来证明「回答质量达标」。质量结论需要更大的模型或专门的评估集。

## 模型与运行方式

| 项目 | 实际值 |
|---|---|
| 模型 | `qwen2.5-1.5b-instruct`（Q4_K_M GGUF，1.1 GB，从 ModelScope 下载后 `ollama create` 导入） |
| 服务 | 本机 Ollama `http://127.0.0.1:11434`，走 OpenAI 兼容端点 `/v1` |
| 后端 | `uvicorn app.main:app`，连接真实 PostgreSQL（`devassist-db-1`）；脚本验证用 `127.0.0.1:8011`，网页验证用 `127.0.0.1:8000`（同一个应用，8000 顺便托管已构建的前端） |
| 检索 | `bm25`（不加载 4 GB 嵌入模型，内存约剩 7 GB） |
| 生成上限 | `CHAT_MAX_TOKENS=512`，总预算沿用默认 30 秒 |

启动命令（项目根目录 `.env` 里没有密钥，因此全部用操作系统环境变量覆盖；pydantic-settings 中 OS 变量优先）：

```bash
cd backend
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy \
    -u all_proxy -u NO_PROXY -u no_proxy \
  DEEPSEEK_API_KEY=local-ollama \
  DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1 \
  DEEPSEEK_MODEL=qwen2.5-1.5b-instruct \
  CHAT_MAX_TOKENS=512 \
  ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
```

必须去掉代理变量：本机 `NO_PROXY` 里含 `[::1]`，httpx 解析会抛 `httpx.InvalidURL: Invalid port: ':1]'`；`http_proxy` 指向的代理也不该参与本机回环请求。聊天客户端本身由 OpenAI SDK 创建，没有像 `semantic.py` 那样显式 `trust_env=False`，所以这一条只能在启动环境里保证。

模型来源与导入（可复现）：

```bash
# 1. 从 ModelScope 拿 Q4_K_M GGUF（约 1.1 GB）
#    https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF
#    qwen2.5-1.5b-instruct-q4_k_m.gguf
# 2. 导入 Ollama
printf 'FROM /tmp/qwen15.gguf\n' > /tmp/Modelfile
ollama create qwen2.5-1.5b-instruct -f /tmp/Modelfile
```

## 实测记录

冒烟脚本 `backend/scripts/smoke-llm-chat.py`：登录 → 上传文档 → 无文档提问 → 每个指定检索方式依次提问 → 读 `/api/v1/chat/history`，结果写 JSON。

```bash
export SMOKE_PASSWORD='换成你的本地测试密码'
cd backend
../.venv/bin/python scripts/smoke-llm-chat.py \
  --email llm-smoke@example.com --password "$SMOKE_PASSWORD" \
  --doc ../docs/VERIFICATION.md --doc ../docs/PHASE2.md \
  --question '后端测试脚本怎么运行？' \
  --question '第二阶段有多少项后端测试通过？' \
  --methods bm25 --out ../.local/llm-chat/report.json
```

上传 `VERIFICATION.md`（8 个片段）和 `PHASE2.md`（11 个片段）。6 次请求的实际结果：

| # | 问题 | 模式 | 状态 | 耗时 | 结果判定 |
|---|---|---|---|---|---|
| 1 | 用一句话解释 FastAPI 和 Pydantic 的关系 | 无文档 | 200 | 1.05 s | 通顺但偏笼统，符合小模型水平 |
| 2 | 后端测试脚本怎么运行？ | bm25 | 200 | 7.93 s | 答出 `scripts/test-local.py` 等真实命令，但混入了第一阶段文档的旧命令，未标 [S1] |
| 3 | 第二阶段有多少项后端测试通过？ | bm25 | 200 | 2.23 s | 先说对「115 项」，随后又自称文档里没有，自相矛盾 |
| 4 | 现在几点？ | 无文档 | 200 | 0.44 s | 正确说明无法提供时间 |
| 5 | 小型人工检索评估一共用了多少个样例？ | bm25 | 200 | 0.99 s | **正确**「12 个样例」，来源 `PHASE2.md:45-57` 命中 |
| 6 | 这个项目支持把视频导出成 MP4 吗？ | bm25 | 200 | 2.87 s | **错误**：资料里没有，模型却答「支持」并编造理由，未按系统提示说明资料不足 |
| 7 | 量子纠缠对猫咪睡姿有什么影响？ | bm25 | 200 | 约 1 s | 检索仍命中 `PHASE2.md:75`（分数 3.42），模型这次正确地说文档里没有 |
| 8 | zzz qqq xxx | bm25 | 422 | 未调用模型 | 正确拦截，返回 `no_document_evidence` |

第 3、6 条是本次最值得记住的两条：检索把正确片段送到了模型面前（`PHASE2.md:45-57` 就是 115 项测试那段），问题出在 1.5B 模型的指令遵循和幻觉控制，不在检索或接口。

第 7、8 条说明「无证据拦截」只对词面完全不重合的问题有效：中文 BM25 用相邻双字分词，常见字组合会给出低分假命中（这与既有多语料评估「8 个无答案问题中 4 个仍返回片段」的结论一致），是否需要 `RAG_MIN_SIMILARITY` 一类的分数阈值需要单独重扫评估，本次没有改代码。

## 网页端验证（浏览器真实点击）

在 `127.0.0.1:8000` 上做了一轮完整点击，确认「页面 → 接口 → 模型 → 页面」整条路走通（此前浏览器验证用的都是模拟接口）：

1. 打开 `/chat`，未登录被重定向到 `/login`；用 `llm-smoke@example.com` 登录后回到 `/chat`。
2. 勾选「使用我的知识库回答」（2 份文档：`VERIFICATION.md`、`PHASE2.md`），检索方式选「关键词检索」。
3. 提问「小型人工检索评估一共用了多少个样例？」，页面显示：

   - 回答：**小型人工评估一共用了12个样例。**（与 HTTP 实测第 5 条一致）
   - 请求标识：`25bcf3f8-05a1-4fff-a08e-bf752de7359d`
   - 本次使用：关键词检索
   - 来源 5 个片段，`[S1] PHASE2.md · 实际验收证据 · 第 45～57 行`，右侧「原文」面板可直接展开该片段（含「后端 115 项测试通过」原文）
   - 历史记录计数由 8 变为 9，说明这一轮也写进了 `chatturn`

4. 浏览器控制台 0 条 error。

截图证据：`.local/llm-chat/browser-live.png`（1280×1397，含回答、请求标识、来源列表与原文面板）。

## 落库与日志证据

- 上表主请求 6 次各写入 `chatturn` 一条，`sources_json` 保存了引用编号、文件名和行号；跑完主请求后 `GET /api/v1/chat/history` 返回 `count=6`。之后的补测（第 7 条）和浏览器验证各再写入一条，页面历史计数显示 9（该计数含此前阶段留下的记录，未逐条回看）。
- 后端日志每条请求都有 `X-Request-ID` 和结构化记录，例如 `elapsed_ms=988.919, status_code=200`；`app.chat:chat_usage` 记录了 `prompt_tokens=1529 completion_tokens=13` 这类用量。
- 日志里没有出现密钥、问题原文或回答正文（按既有约定打码）。
- 原始证据：`.local/llm-chat/report.json`、`.local/llm-chat/report2.json`（均在 `.local/`，不进 Git）。

## 已验证 / 未验证

已验证：

- 真实模型调用成功，答案经 `/api/v1/chat` 返回并落库，含来源片段与请求标识。
- 无文档模式和文档模式都能走通；无证据的问题按既有逻辑返回 422（此前已测）。
- Ollama 会忽略 DeepSeek 专有的 `thinking: {type: disabled}` 字段（直接请求与经后端各测一次均 200），因此同一份代码可切换提供商。
- 用量与耗时在日志中可追踪。

未验证 / 已知限制：

1. **DeepSeek 云端仍未验证**。本次结果不能写成「DeepSeek 真实回答」。
2. `vector` / `hybrid` 检索未在本次一起跑（需要启动 `zebao-rag` worker 并加载 4 GB 嵌入模型，当时可用内存约 7 GB、swap 已用近 60%）。
3. 网页端只用「关键词检索 + 2 份文档」跑了一轮（见上一节），`vector` / `hybrid` 在页面上仍未点过；历史侧栏只验了计数增长，没逐条回看。
4. 聊天仍是单轮，不带历史上下文。
5. 1.5B 模型的引用标注与拒答不可靠，见上表第 3、6 条。若要在页面上做演示，建议换更大的本地模型或 DeepSeek 云端。
6. `CHAT_TOTAL_TIMEOUT_SECONDS` 上限为 30 秒，本次靠 `CHAT_MAX_TOKENS=512` 控制时长；再长的回答会被预算截断。
7. 用 `DEEPSEEK_API_KEY=local-ollama` 只是让「未配置密钥」检查通过，不是真实凭据；不要把这个写法带进线上配置。
