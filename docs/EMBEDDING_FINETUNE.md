# 嵌入模型 LoRA 微调（Qwen3-VL-Embedding-2B）

DevAssist 的向量检索走本地 RAG worker（`services/zebao-rag`），查询和文档都用
`Qwen/Qwen3-VL-Embedding-2B` 编码成 2048 维向量。本文记录这个模型的 LoRA
微调链路：语料怎么来、问题怎么生成、怎么训练、怎么证明有用（或没用）。

训练与评估都在这台 M5 / 32GB 上完成，不依赖云服务。

## 一、结论先看

| 评估集 | 方案 | Top1 | Recall@5 | MRR@5 |
| --- | --- | --- | --- | --- |
| examples 30 问（人工标注） | BM25（项目原有基线） | 0.727 | 0.886 | 0.811 |
| examples 30 问（人工标注） | 嵌入基线 | 0.818 | 0.955 | 0.886 |
| examples 30 问（人工标注） | 嵌入 + LoRA | 0.773 | 0.955 | 0.841 |
| 项目文档验证集 45 问（自动生成） | 嵌入基线 | 0.622 | 0.911 | 0.731 |
| 项目文档验证集 45 问（自动生成） | 嵌入 + LoRA | 0.756 | 0.956 | 0.824 |

读法：

- 在**同领域、未参与训练**的项目文档上，Top1 从 0.622 提到 0.756（+13.4 个百分点），
  MRR 从 0.731 到 0.824，说明 adapter 学到了“中文项目文档提问 → 对应片段”的匹配方式。
- 在人工标注的 30 问上 Top1 反而降了 4.5 个百分点（0.818 → 0.773），Recall 不变。
  这批语料是合成的演示文档，和训练语料的文风不同，属于**分布外轻微回退**。
  这也说明：微调不是无脑加分，必须两套集都看。
- 训练损失从 0.4112 降到 0.0043（20 类对比的随机水平是 ln(20)≈3.0），
  已经接近把训练对背下来。因此**验证集数字比训练损失更有意义**。

样本量小（22 个有答案问题 / 45 个验证问题），这些数字是趋势证据，不是生产结论。

## 二、数据从哪来

项目里没有现成的“问题 → 片段”检索对。链路是反向构造的：

1. **语料**：`scripts/finetune/corpus.py` 读 DevAssist 自己的文档
   （README、COLLABORATION、CONTRIBUTING、deployment*、development 和 `docs/*.md`），
   复用 RAG worker 的分块逻辑（按标题切段 + 500 字符窗 / 80 重叠）。
   结果：**227 个片段**（平均 389 字符），丢弃 6 条过短片段。
2. **按来源留出验证集**：`OPERATIONS.md`、`ZEBAO_RAG_INTEGRATION.md`、
   `RETRIEVAL_EVAL_REPORT.md` 三份文件整体不参与训练 → 训练 174 / 验证 53 片段。
   按文件留出而不是随机切块，避免同一段落一半在训练一半在验证造成泄漏。
3. **生成问题**：`scripts/finetune/generate_queries.py` 用本机 Ollama 的
   `qwen-fast:latest` 给每个片段写一个中文问题（要求不超过 25 字、不许照抄原句）。
   实测 **200 条**（训练 155 / 验证 45），耗时约 17 分钟。

踩过的坑：`qwen-fast` 是思考模型，Ollama 默认会把 `num_predict` 的 token 全花在
思维链上，`content` 返回空串，一条问题都拿不到。必须在请求里加
`"think": false`（见脚本注释），否则跑一晚也是 0 条。

## 三、训练怎么跑

`scripts/finetune/train_lora.py`：

| 项 | 取值 |
| --- | --- |
| 基座 | `Qwen/Qwen3-VL-Embedding-2B`（bf16，Apple MPS） |
| LoRA | r=8，alpha=16，dropout=0.05，target=`q_proj/k_proj/v_proj/o_proj` |
| 可训练参数 | 3,211,264（占全模型 0.15%） |
| 池化 | 取最后一个非 pad token（对齐模型自带的 lasttoken pooling）+ L2 归一化 |
| 损失 | 批内对称 InfoNCE，`logits = q @ p.T * 20`，双向交叉熵取平均 |
| 超参 | batch 8、lr 1e-3、3 epoch、60 步、梯度裁剪 1.0 |
| 耗时 | 258 秒（约 4.3 秒/步） |
| 产物 | `services/zebao-rag/.local/finetune/adapter`（12 MB） |

训练前后都验证过：`--max-steps 2` 冒烟先跑通，再跑完整 60 步。

```bash
cd services/zebao-rag
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy -u NO_PROXY -u no_proxy \
  .venv/bin/python scripts/finetune/train_lora.py
```

## 四、怎么证明有效

`scripts/finetune/eval_retrieval.py` 在**同一台机器、同一批片段**上跑三次排序：

1. BM25（项目原有基线，`app.knowledge.rank_chunks`）；
2. 嵌入基线（余弦相似度）；
3. 嵌入 + LoRA（`SentenceTransformer.load_adapter` 后重算向量）。

评测口径和项目原有的 `docs/RETRIEVAL_EVAL_REPORT.md` 一致：命中要求文件名正确、
片段覆盖标注行号、且包含标注引用，按 Top1 / Recall@5 / MRR@5 计分。
复算出的 BM25 基线（0.727 / 0.886 / 0.811）与既有报告完全一致，说明这套复算没跑偏。

```bash
cd backend   # app.core.config 按相对路径读 ../.env
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy -u NO_PROXY -u no_proxy \
  ../services/zebao-rag/.venv/bin/python ../services/zebao-rag/scripts/finetune/eval_retrieval.py
```

产物：`services/zebao-rag/.local/finetune/eval_report.json`（含逐条失败样例）。

## 五、接到项目里怎么用

RAG worker 的编码器加了环境变量开关：

```bash
export ZEBAO_RAG_ADAPTER=/绝对路径/services/zebao-rag/.local/finetune/adapter
```

- 设置后 `zebao_rag.embedder.get_model()` 会在加载完基座后 `load_adapter`，
  查询和入库都走高配向量；不设置就是原基线。
- **切换后必须重建索引**：库里已有向量是基座算的，和微调后的查询向量不在同一个空间。
  用 `scripts/build` 或 CLI 的 `build` 子命令重跑一遍再检索。
- 训练只用了文本对。图片走同一个 backbone，理论上向量也变了，但**图片侧没有评估**；
  以图搜图场景先按基线用，要做先补一套图片评估再开开关。

devassist 这条链路上还有几个必须一起改的开关：

```bash
# 后端（devassist/backend）
RAG_WORKER_ENABLED=true      # 默认 false，不开就只有 BM25，向量检索直接退化成关键词
RAG_VECTOR_STORE=chroma      # worker 默认引擎；用 Milvus 时要先把四件套起起来
RAG_EMBEDDING_IDENTITY=qwen3-vl-text-v1+lora-r8   # 参与 Redis 检索缓存键
```

`RAG_EMBEDDING_IDENTITY` 是这一轮补的：缓存键原本写死 `qwen3-vl-text-v1`，
微调前后会互相复用命中结果（TTL 180 秒），做基线/微调对比时会拿到假数据。现在模型标识由
配置决定，换模型或换适配器时必须同步改。

另外 devassist 的文档检索是**请求时现算向量**（worker `/rank`），没有持久化向量
索引，所以这里没有「重建索引」这一步；要重建的是 `services/zebao-rag` 自己的
Chroma 集合（`zebao_multimodal` / `zebao_personal` / `zebao_japanese`）。

## 六、已验证 / 未验证

已验证：

- 语料 227 片段、问题 200 条（155 训练 / 45 验证）真实产出，文件在
  `services/zebao-rag/.local/finetune/`；
- 训练 60 步跑通，adapter 12 MB 落盘，`train_meta.json` 记录超参与耗时；
- 两套评估集上 baseline / LoRA 的对比数字（见第一节），复算 BM25 与既有报告一致；
- `ZEBAO_RAG_ADAPTER` 分支代码可编译，目录不存在时抛明确错误；
- **适配器真实加载已验证**（2026-10-20，MPS）：同一句「如何配置本地检索服务？」带/不带
  adapter 各编码一次，向量都是 2048 维、模长归一化，余弦只有 0.70 / 0.69，
  最大分量差 0.10 —— 说明 adapter 确实改写了输出，不是加载了却没用上。
- **接入 devassist 的真实检索链路已验证**（2026-10-20）：RAG worker 带 adapter 起在
  8765，后端用 `RAG_WORKER_ENABLED=true`、`RAG_VECTOR_STORE=chroma`
  重启后，用 `aigc-ui@example.com` 上传 `docs/AIGC.md`（16 块）与
  `docs/EMBEDDING_FINETUNE.md`（8 块），再从
  `POST /api/v1/documents/search` 走 `vector` / `hybrid` 两种
  方式检索 3 个真实问题，结果全部命中预期文档：

  | 问题 | 期望文档 | adapter 向量 | adapter hybrid | 基线向量 | 基线 hybrid |
  | --- | --- | --- | --- | --- | --- |
  | 跑真实视频生成前要怎么释放内存？ | AIGC.md | #1 | #1 | #1 | #1 |
  | LoRA 微调用了什么参数，前后指标提升了多少？ | EMBEDDING_FINETUNE.md | #1 | #1 | #1 | #1 |
  | 浏览器端点击验证了哪几条用例？ | AIGC.md | #1 | #1 | #1 | #1 |

  只有 2 篇文档、问题又偏关键词，所以两边都排 #1——这组只能证明**链路通**（真实
  PostgreSQL + 真实 worker + 真实 adapter + RRF 融合），不能证明微调收益；收益看第一节
  的 45/30 条评估集。原始结果在 `.local/zebao-rag/real-chain-smoke.json`。
  同一批查询下 adapter 的余弦更平（0.30–0.52），基线更陡（0.58–0.72），说明微调后相似度
  分布变了，阈值 `RAG_MIN_SIMILARITY=0.3` 需要按新分布重新校准。

**踩到的坑（已修）**：直接 `load_adapter` 会偶发 `Segmentation fault: 11`（exit 139），
崩在 `transformers/core_model_loading.py` 的线程池 `_materialize_copy` 里
（transformers 5.19 默认用线程池并发加载权重，Apple Silicon + MPS 下不稳定）。
修法是在 **import transformers 之前**设 `HF_DEACTIVATE_ASYNC_LOAD=true` 改主线程同步加载；
`src/zebao_rag/embedder.py` 与 `scripts/finetune/eval_retrieval.py` 都已经在文件顶部
`os.environ.setdefault` 设好，单独写脚本调 `load_adapter` 时要自己带上。

未验证：

- **带 LLM 生成的完整问答**：`POST /api/v1/chat` 取到检索结果后返回 503
  `model_not_configured`（本机 `.env` 没填模型密钥），所以只验证到
  「检索 → 引用来源」这一段，答案生成没跑；填好密钥后可用同一问题复跑。
- 图片检索侧的效果变化；
- 更大规模、真实用户提问分布下的收益；
- `RAG_MIN_SIMILARITY` 在微调向量分布下的最优值（现在仍是 0.3，未重新扫）。

## 七、复现顺序

```bash
cd services/zebao-rag

# 1. 造语料（227 片段，自动留出验证源）
.venv/bin/python scripts/finetune/corpus.py

# 2. 生成问题（约 17 分钟，需 Ollama 在线）
.venv/bin/python scripts/finetune/generate_queries.py --split all --min-chars 150

# 3. 训练（约 4-5 分钟，MPS）
.venv/bin/python scripts/finetune/train_lora.py

# 4. 评估（约 3 分钟，cwd 必须是 backend）
cd ../../backend
../services/zebao-rag/.venv/bin/python ../services/zebao-rag/scripts/finetune/eval_retrieval.py
```

依赖：`peft`、`accelerate`（训练）与 `sqlmodel`、`pydantic-settings`、
`email-validator`（评估脚本复用后端 `app.knowledge` 时需要，已装在 RAG 的 venv 里）。
