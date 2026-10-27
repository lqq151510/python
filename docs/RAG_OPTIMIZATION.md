# BM25 排序与 Agent 页面状态优化

记录日期：2026-10-23，Asia/Shanghai。对照基线提交：`e52d45788b64260e11466b33a34447c4337e60cd`。

本轮改善了既有诊断集中的长文排序，并补齐 Agent 页的加载、错误、重试和请求交错保护。独立留出集的新旧结果相同，尚无泛化提升证据；无答案误命中仍是明确的剩余问题。本轮没有数据库结构迁移或新增依赖；Git 交付状态以提交历史和远端分支为准，不代表已发布。

## 修改范围与原理

`backend/app/knowledge.py` 中的 BM25 保持 `k1=1.5`，仅把 `b` 从 0.75 调整为 0.5。分词、分块、候选范围与前五条返回策略不变；原有五份语料、30 题诊断标注 `examples/retrieval-eval.json` 和固定门槛 `examples/retrieval-eval-gate.json` 均未改动。

`b` 控制词频分数受文档长度归一化影响的强度；降低它会减弱较长片段相对于短片段受到的长度惩罚，`k1` 则影响词频饱和。本项目在已有诊断集上观察到两道长文题改善，不能据此认定所有长文或真实知识库都会受益。参数含义参考 [Apache Lucene BM25Similarity 官方文档](https://lucene.apache.org/core/9_12_2/core/org/apache/lucene/search/similarities/BM25Similarity.html)。本项目沿用自身 BM25 实现，没有引入 Lucene 依赖。

Agent 页对应 `frontend/src/routes/_layout/agent.tsx` 与 `frontend/src/lib/agent.ts`：

- 会话列表与详情分别展示正在加载、失败及手动重试，只有成功的空响应才显示空态。
- 选中会话的详情成功加载前禁止续发，避免在对话上下文尚不明确时发送。
- 列表、详情请求接入取消信号；切换会话或离页会取消不再需要的查询。
- 发送期间禁用会话切换与新会话入口，防止迟到的发送结果覆盖当前选择；保留既有样式与路由。

## 原诊断集对照

诊断集含 30 题：22 个有答案问题、8 个无答案问题。严格通过要求：有答案题首条正确且前五条召回全部标注证据，无答案题返回空结果。Top-1、Recall@5 和 MRR@5 只统计有答案问题。

| 指标 | 基线 `b=0.75` | 当前 `b=0.5` |
|---|---:|---:|
| 严格通过 | 20/30 | 22/30 |
| Top-1 首条命中率 | 72.7% | 81.8% |
| Recall@5 平均证据召回率 | 88.6% | 90.9% |
| MRR@5 首条证据排名倒数均值 | 0.811 | 0.848 |
| 无答案正确返回空结果 | 4/8 | 4/8 |
| 无答案仍返回片段 | 4/8 | 4/8 |

新增通过 `L03`、`L05`，没有原通过题退化。参数选择使用了已知诊断集，因此这里是针对已有问题的诊断改进，不能将 20/30 → 22/30 直接解释为泛化能力提升。固定门槛通过也不等于全部严格通过。

## 独立留出集与实验边界

新增 `examples/retrieval-eval-holdout.json`，基于同一五份文档独立标注 24 题：14 个有答案问题、10 个无答案问题。标注作者仅依据语料编题，没有读取已有问题、检索实现或排序结果；完成后保存并冻结文件，SHA-256 为：

```text
318809438abf6a2ead5f0a47242495a68f2ec8c912fbb297f3a8080d44977643
```

冻结后作者看到了 Agent 列表中的参数元信息，未据此修改标注。候选参数已在首次留出集评估前固定，之后没有依据留出结果继续调参。该流程提供独立标注与事先固定候选的对照，但不声称全程双盲，也不是另一份独立语料上的外部验证。

| 指标 | 基线 `b=0.75` | 当前 `b=0.5` |
|---|---:|---:|
| 严格通过 | 14/24 | 14/24 |
| 有答案题 Top-1 | 100%，14/14 | 100%，14/14 |
| 有答案题 Recall@5 | 100% | 100% |
| 有答案题 MRR@5 | 1.000 | 1.000 |
| 无答案正确返回空结果 | 0/10 | 0/10 |
| 无答案仍返回片段 | 10/10 | 10/10 |

本次留出复验没有观察到提升或退化。全部无答案题仍返回片段，说明关键词相关度不足以判断资料是否包含所需操作证据；片段返回不等于模型已经生成错误答案。完整问题、标注与失败片段见 [留出集评估报告](RETRIEVAL_HOLDOUT_REPORT.md)。

本地对照证据保存在 `.local/rag-optimization/comparison.json`、`frozen.json` 以及 `diagnostic-*.json`、`holdout-*.json` 中，均未纳入 Git。`frozen.json` 同时记录候选参数、基线提交、留出集指纹和候选检索代码指纹。

## 复现当前版本

以下命令从 `backend/` 执行，使用现有 `.venv`，不需要模型密钥、数据库或网络。报告显式写入 `.local/rag-optimization/`，避免覆盖已保存的历史文档。

原 30 题诊断集与固定回归门槛：

```bash
../.venv/bin/python scripts/evaluate-retrieval.py \
  --dataset ../examples/retrieval-eval.json \
  --gate ../examples/retrieval-eval-gate.json \
  --json-output ../.local/rag-optimization/reproduced-diagnostic.json \
  --report ../.local/rag-optimization/reproduced-diagnostic.md
```

冻结的 24 题留出集：

```bash
../.venv/bin/python scripts/evaluate-retrieval.py \
  --dataset ../examples/retrieval-eval-holdout.json \
  --json-output ../.local/rag-optimization/reproduced-holdout.json \
  --report ../.local/rag-optimization/reproduced-holdout.md
```

默认生成报告返回 0，不代表每道题通过；原集使用 `--gate` 验证固定回归底线。任一命令额外加 `--strict` 后，当前版本都会因仍有未通过问题返回 1。格式或标注输入错误返回 2。两个数据集的分母和题目不同，不应直接比较其百分比作为前后提升。

## 本轮工程验证

| 检查 | 实际结果与范围 |
|---|---|
| 临时 PostgreSQL 完整后端回归 | 262 项通过、2 项跳过、1 条既有 Starlette/httpx 弃用警告 |
| 隔离运行检查 | 23 项通过；临时 API、临时 PostgreSQL 与本机已有 Ollama `qwen2.5-1.5b-instruct:latest`，覆盖登录、检索、回答落库、账号隔离、历史幂等和代码工作区 |
| Agent Chrome 浏览器回归 | 7 项通过；使用模拟 API，覆盖状态、重试、请求取消和发送期间锁定会话 |
| 既有 Chrome 浏览器回归 | 34 项通过，19.0 秒；知识库配置包含 16 项聊天、16 项知识库和 2 项学习实验测试，使用模拟 API |
| 前端构建 | 通过 |

完整后端与运行检查证据为 `.local/isolated-validation/c63afb73f508/summary.json` 和 `runtime.json`；该轮临时 API、数据库容器及临时目录均已清理。此前 260 项的历史验收见 [可靠性修复记录](RELIABILITY_FIXES.md)。

两组浏览器合计 41 项通过。既有知识库配置首次使用其继承的默认 `webServer` 启动时，60 秒内未就绪；之后显式启动独立服务并设置 `PLAYWRIGHT_BASE_URL=http://127.0.0.1:5177`，34 项全部通过，临时服务已停止并核对没有端口监听。首次超时根因尚未确定，不能据此归因 IPv6，也未修改共享聊天测试配置。

若默认测试服务启动不稳定，可在两个终端中复现既有 34 项，两个终端均从 `frontend/` 执行。终端一保持前台服务：

```bash
../node_modules/.bin/vite --host 127.0.0.1 --port 5177 --strictPort
```

终端二运行测试：

```bash
PLAYWRIGHT_BROWSER_CHANNEL=chrome PLAYWRIGHT_BASE_URL=http://127.0.0.1:5177 \
  ../node_modules/.bin/playwright test -c playwright.knowledge.config.ts
```

测试结束后在终端一用 Ctrl+C 停止自己启动的服务。Agent 配置独立使用 5176 端口，可从 `frontend/` 运行 `PLAYWRIGHT_BROWSER_CHANNEL=chrome ../node_modules/.bin/playwright test -c playwright.agent.config.ts`，由测试进程启动并清理服务。

本轮没有验证真实向量服务、多库检索或 ComfyUI 视频生成，也没有用小模型的单个成功样例证明回答质量提升。

## 剩余问题与回滚

下一步需要单独设计无答案判断与证据充分性评估，保留当前失败样例，并用之后新增、事先冻结的资料和问题复验；本轮不依据留出集结果追加规则或继续调参。

仅回退排序行为时，可将 `BM25_B` 恢复为 0.75，并同步回退本轮排序回归测试与参数断言，重新运行原集固定门槛与留出报告。完整回退应定点恢复本轮涉及的检索代码、相关测试和 Agent 页文件，移除新增的留出集、报告、测试配置与说明，保留无关本地改动。没有数据库迁移或新依赖需要撤销，不需删除数据库、账号、文档或数据卷。
