# AIGC 视频生成（本地 ComfyUI + MiniMax H3）

本项目把「本机跑 MiniMax H3 生成视频」接成一条异步任务链路：页面提交即返回
`job_id`，后台单并发执行器调度 ComfyUI，产物落在本机磁盘，任何请求都不会
等待一次完整推理。

## 为什么必须是异步

单条 5 秒 864×480 的片段在本机（M5 / 32GB 统一内存 / Turbo 4 Fast）约需 12 分钟，
480×288 约 4.3 分钟（节点内实测公式 `T ≈ 16 + 0.00173 × 宽 × 高` 秒）。HTTP 请求
等不起，所以链路拆成「建任务 → 后台执行 → 轮询进度」。

## 数据流

```
前端表单 /aigc
  -> POST /api/v1/aigc/jobs（或 /jobs/image，multipart）
  -> 校验参数 → AigcJob 落库（status=queued）→ 立即返回 job_id
  -> aigc_runner 单并发线程：claim 任务（条件 UPDATE，避免双领）
     -> ComfyUI POST /prompt（API 格式工作流）
     -> 轮询 GET /history/{prompt_id}
     -> GET /view 取回 mp4 → 写入 AIGC_OUTPUT_DIR/{owner_id}/{job_id}.mp4
     -> 更新 status / progress / output_relpath
  -> 前端轮询 GET /api/v1/aigc/jobs/{id}，完成后 GET …/video 播放
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/aigc/config` | 是否启用、ComfyUI 是否可达、默认参数与限制 |
| GET | `/api/v1/aigc/jobs` | 当前账号最近 20 条任务 |
| POST | `/api/v1/aigc/jobs` | 文生视频（JSON） |
| POST | `/api/v1/aigc/jobs/image` | 图生视频（multipart：`first_frame` + `prompt` + `params` JSON 字符串） |
| GET | `/api/v1/aigc/jobs/{id}` | 任务详情（progress / status_message / error） |
| POST | `/api/v1/aigc/jobs/{id}/cancel` | 取消（运行中会调用 ComfyUI `/interrupt`） |
| GET | `/api/v1/aigc/jobs/{id}/video` | 取回 mp4 |
| DELETE | `/api/v1/aigc/jobs/{id}` | 删除终态任务与其产物 |

失败统一返回 `{"detail": {"code": "...", "message": "..."}}`，错误码：
`aigc_disabled`(409)、`aigc_queue_full`(429)、`aigc_comfy_unreachable`(503)、
`aigc_comfy_bad_response` / `aigc_comfy_rejected` / `aigc_execution_failed` /
`aigc_output_missing` / `aigc_output_empty`(502)、`aigc_timeout`(504)。

## 参数硬约束（来自节点源码，不要放宽）

- `width` / `height` 必须是 32 的倍数，否则节点直接 `raise ValueError`；
  路由层先用 `VideoParams` 校验，返回 `{field_name} 必须是 32 的倍数`。
- 可选范围：宽 256–1280、高 256–720、时长 5.0–15.0 秒、seed 0–2^64-1。
- 速度档：`Turbo 4 Fast`（4 次前向，推荐起步）/ `Turbo 8 Balanced` / `Full 20 Quality`；
  加速只影响步数，不改变分辨率。
- 标准 ComfyUI 的 `Load LoRA` 节点插不进这个 MLX 节点（它不输出标准 MODEL 对象），
  所以生成链路当前不支持叠加社区 LoRA。

## 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AIGC_ENABLED` | `false` | 关着时接口返回 409，页面显示「未启用」 |
| `AIGC_COMFY_URL` | `http://127.0.0.1:8188` | 只允许本机回环 HTTP，禁止凭据与路径 |
| `AIGC_OUTPUT_DIR` | `backend/.local/aigc` | 产物与上传首帧的根目录 |
| `AIGC_MAX_QUEUE_DEPTH` | `4` | 排队中 + 运行中的任务上限，超过返回 429 |
| `AIGC_POLL_INTERVAL_SECONDS` | `5` | 轮询 ComfyUI 的间隔 |
| `AIGC_JOB_TIMEOUT_SECONDS` | `3600` | 单任务超时，超时调用 `/interrupt` 并置 failed |
| `AIGC_MAX_PROMPT_CHARS` | `2000` | 提示词上限 |

## 内存预算（本机 32GB 决定顺序）

`backend/app/core/device.py` 给出统一体检：视频生成预算 20GiB、嵌入微调 14GiB、
嵌入编码 6GiB、RAG 栈 6GiB，swap 使用率超过 95% 一律拒绝。实测节律：

```bash
cd backend && ../.venv/bin/python -m app.core.device   # 打印 JSON 并返回退出码
scripts/dev-stack.sh mode video                        # 只留 PostgreSQL，腾内存给视频
scripts/dev-stack.sh mode train                        # 关掉 Milvus，腾内存给微调
```

一次 H3 生成峰值约 16–20GB，LoRA 训练也要 10–15GB，两者不能同时进行：
先做完视频，再 `scripts/dev-stack.sh mode train`。

## 启用步骤

```bash
# 1. 起 ComfyUI（约 8188）；确认节点已加载
ollama list >/dev/null 2>&1 || true            # 与视频无关，仅提示本机有 Ollama
ls ~/ComfyUI-H3/models/minimax_h3/loras/      # 权重就位

# 2. .env 里打开开关
#    AIGC_ENABLED=true

# 3. 迁移 + 起后端
cd backend && ../.venv/bin/alembic upgrade head
cd .. && .venv/bin/uvicorn app.main:app --reload

# 4. 冒烟
curl -s localhost:8000/api/v1/aigc/config -H "Authorization: Bearer <token>"
```

## 测试

```bash
# 不连数据库的纯单元测试（ComfyUI 用 httpx.MockTransport 模拟）
cd backend && ../.venv/bin/python -m pytest tests/test_aigc.py tests/test_device.py --noconftest -q

# 前端
cd frontend && ../node_modules/.bin/tsc -p tsconfig.build.json
```

`--noconftest` 是必需的：`backend/tests/conftest.py` 有一个 session 级的数据库
fixture，会去连 PostgreSQL。

浏览器端点击流程（需要先起假 ComfyUI、后端、前端）：

```bash
# 1. 假 ComfyUI
.venv/bin/python backend/scripts/fake-comfyui.py --port 8189
# 2. 后端指向假服务
cd backend && AIGC_ENABLED=true AIGC_COMFY_URL=http://127.0.0.1:8189 \
  AIGC_POLL_INTERVAL_SECONDS=0.3 \
  ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 3. 前端（vite 默认只监听 ::1，所以用 localhost）
cd frontend && bun run dev
# 4. 浏览器测试账号 + 用例
cd backend && ../.venv/bin/python scripts/seed-ui-user.py
cd frontend && PLAYWRIGHT_BROWSER_CHANNEL=chrome \
  PLAYWRIGHT_BASE_URL=http://localhost:5173 \
  bunx --no-install playwright test -c playwright.aigc.config.ts
```

## 联调用的假 ComfyUI

没有真实模型时也能验证整条链路：`backend/scripts/fake-comfyui.py` 按 ComfyUI 的
HTTP 契约回应，第一次 `/history` 返回空（模拟排队），之后返回成功产物，
`/view` 返回一小段字节，`/debug/state` 暴露收到的上传与中断次数。

```bash
.venv/bin/python backend/scripts/fake-comfyui.py --port 8189

cd backend && AIGC_ENABLED=true AIGC_COMFY_URL=http://127.0.0.1:8189 \
  AIGC_POLL_INTERVAL_SECONDS=0.3 \
  ../.venv/bin/python -m uvicorn app.main:app --port 8011
```

## 真实 ComfyUI 联调结果（2026-10-20）

本机 ComfyUI 0.41.0 + `ComfyUI-MiniMax-H3-MLX-SolAttn`，权重 `models/minimax_h3` 共 92 GB。
**能跑通的档位是 `4-bit` + `memory_mode=stream5`**；选 `4-bit-pruned` 会直接报
`FileNotFoundError: MiniMax H3 4-bit-pruned model setup is incomplete`（缺 `config.json`、
`model.safetensors.index.json`、`quant_config.json`、`h3_silu_temb_grid.safetensors`，
这档权重本机还没用 `scripts/setup_comfyui.py --profile 4-pruned` 生成）。用户模板
`MiniMax_H3_32GB_Stream5.json` 用的也是 `4-bit` + `stream5`。

| 场景 | 走法 | 任务耗时 | 产物 |
| --- | --- | --- | --- |
| 文生视频 256×256 / 5s / Turbo4 | `POST /aigc/jobs` 全链路 | 106.3 s | 362,041 B |
| 图生视频 256×256 / 5s / Turbo4 | `POST /aigc/jobs/image` 全链路 | 146.0 s | 894,625 B |
| 文生视频（只用 `aigc.ComfyClient`） | 直接 submit/poll/download | 115.7 s | 613,005 B |

产物经 `ffprobe` 核对：`h264` 256×256 24 fps **124 帧** + `aac` 音轨，时长 5.166688 s。
图生视频的首帧确实生效（抽帧对比：输入是织物特写，输出从同一画面起运动）。

ComfyUI 侧阶段耗时（256×256，日志原值）：Qwen stage1 12.9 s / peak 13.79 GB，stage2
5.2 s / peak 12.97 GB；denoise 4 步 16.1 + 16.8 + 17.3 + 18.1 s / peak 3.46 GB；
video VAE 18.3 s / peak 11.60 GB；audio VAE 4.3 s / peak 3.13 GB。图生视频多一个
keyframe stage（4.1 s / peak 15.15 GB、64 行 conditioning）。**峰值出现在 Qwen 与
keyframe 这两个阶段（约 15 GB），不是 denoise 本身** —— 内存护栏按 20 GiB 预算判断是对的。

跑之前先腾内存：`scripts/dev-stack.sh stop` 只留 PostgreSQL，ComfyUI 与后端各自一个
进程；跑完把两个进程停掉再起 Milvus 做检索。

## 浏览器端联调（Playwright + 假 ComfyUI，2026-10-20）

`frontend/tests/aigc.spec.ts` 走真实浏览器 + 真实前端 + 真实后端 + 真实 PostgreSQL，
只有 ComfyUI 换成假服务（假服务返回的不是可播放视频，所以播放器会一直转圈，这是预期）。

| 用例 | 断言的用户可见结果 | 耗时 |
| --- | --- | --- |
| 文生视频 | 提交后出现「任务已提交，排队中。」→ 列表项变「已完成」→ 详情出现 `video` 且 `src` 是 `blob:` | 5.8 s |
| 图生视频 | 选「图生视频」→ 上传首帧 → 列表项同时显示「图生视频」「已完成」 | 5.6 s |
| 取消任务 | 慢任务详情出现 `progress` → 点「取消任务」→ 提示「已取消该任务。」→ 列表项变「已取消」 | 1.4 s |
| 删除任务 | 点「删除任务与产物」→ 提示「任务和产物已删除。」→ 列表项数量减一 | 1.4 s |

4 条用例全部通过（共 14.9 s）。假服务 `/debug/state` 记录到 1 次 `/upload/image`，
说明图生视频的首帧是从浏览器上传 → 后端 → ComfyUI 真的走完了。

这一轮顺带修掉两个只有浏览器才能真正暴露的问题：

- **双层外壳**：`_layout.tsx` 只把 `/chat`、`/knowledge`、`/learning` 交给
  `DevAssistLayout`，`/aigc` 落到了模板侧栏里，而页面自己又包了一层
  `DevAssistLayout`，结果屏幕上出现两个侧栏 + 模板页脚。修法是把 `/aigc`
  加进 `_layout.tsx` 的 DevAssist 路径列表，页面自身只保留 `<DevAssistHeader aigc />`
  与内容（与 `chat.tsx` 一致）。
- **危险按钮配色**：`da-text-button da-danger` 两个类冲突，后者的白字被覆盖成
  `--primary`，红底红字几乎不可读。新增 `.da-text-danger` 只改文字颜色，
  按钮改成「红图标 + 红字」的无底色样式。

为了验证取消流程，假 ComfyUI 增加了慢任务开关：提交的提示词里带「慢一点」时，
`/history` 永远返回排队中（`SLOW_POLLS = 100_000`），这样取消不会和执行器抢时间；
`FAKE_COMFY_POLLS_BEFORE_READY` 环境变量可整体调节正常任务的排队轮数。

截图证据：`.local/aigc-demo/ui-aigc-fake.png`（单侧栏、导航高亮、已完成任务、
内嵌播放器、可读的删除按钮）。

## 已验证 / 未验证

已验证（2026-10-20，真实 PostgreSQL + 真实 FastAPI + 真实后台执行器，假 ComfyUI）：

- 迁移 `b7c4a1d9e2f0` 在真实数据库上从 `c7d3e18a04f2` 升级成功（建表 + 4 个索引）。
- `/aigc/config`：`enabled=true`、`runner_running=true`、`comfy_reachable=true`。
- 文生视频：`POST /jobs` → 201，后台执行 → `succeeded`、`progress=100`，
  `GET /jobs/{id}/video` 返回 200 与 `video/mp4`。
- 图生视频：`POST /jobs/image`（multipart）→ 201 → `succeeded`；
  假服务记录到 1 次 `/upload/image`，说明首帧确实在执行时传给了 ComfyUI。
- 取消：`POST /jobs/{id}/cancel` → 200，任务终态 `canceled`。
- 参数校验：`width=100` 与空提示词均返回 422（`greater_than_equal` / min_length）。
- 删除：`DELETE /jobs/{id}` → 204，随后 `GET` 返回 404，产物与首帧一并清理。
- 单元测试 42 passed（`tests/test_aigc.py` 29 + `tests/test_device.py` 13）；
  前端 `tsc -p tsconfig.build.json` 与 `vite build` 通过（aigc chunk 11.86 kB）。
- 浏览器端 Playwright 4 条用例全部通过（真实前端 + 后端 + PostgreSQL，假 ComfyUI）：
  文生视频到内嵌播放器、图生视频含首帧上传、取消、删除；详见「浏览器端联调」一节。

已验证（2026-10-20，真实 ComfyUI + 真实 H3 权重）：上面「真实 ComfyUI 联调结果」
一节的两条全链路任务，说明手写的 API 格式图被 `/prompt` 接受，`/history` 的
`outputs` → `saved_videos` → `/view` 下载链路与产物结构都对得上。

未验证：

- 浏览器端点击**真实 ComfyUI**：本轮浏览器验证用假 ComfyUI（页面交互与数据流真实，
  产物是假字节），真实出片是接口层验证的（见上一节）。
- 更高分辨率与更长时长（480×288 / 864×480 / 15 s）在本机的实际耗时与峰值，
  目前只有节点 README 的经验公式 `T ≈ 16 + 0.00173 × 宽 × 高`（秒）。
- 非 Turbo 档（`Full 20 Quality`）与 `attention16-mlp8-pruned` 等档位。
