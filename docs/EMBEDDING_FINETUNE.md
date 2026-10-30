# 嵌入模型 LoRA 微调（Qwen3-VL-Embedding-2B）

> 2026-10-30 协议更新：以下数字仍是历史实验，使用旧的无查询指令评估，
> 不代表线上检索收益。本次已让评估脚本在查询端使用 worker 的文本检索指令，
> 文档端不显式传入指令，但模型如配置默认 prompt，编码器仍可能自动应用；
> 输出改为 `eval_report_online_protocol_v1.json`，保留旧结果。
> 本次没有重新运行模型或训练；新协议的效果、完整候选集与真实线上表现仍待验证。
>
> **训练输入修正**：新训练复用 SentenceTransformer 的 prompt 解析、预处理及池化；
> 查询使用 worker 的文本指令，文档使用模型默认 prompt。无法确认默认 prompt、
> 设置训练最大长度或确认 LoRA 已接入前向路径时会停止；`train_meta.json` 记录
> prompt 名称与摘要、训练长度
> 和模型加载时的默认长度。训练默认 `--max-length=256`，可能与线上实际长度不同；
> 元数据不证明线上长度已核验。训练使用 BF16、训练模式及 float32 归一化，与线上
> 的模式/精度可能不同；上述修正不代表向量数值一致或实际检索效果已经验证。
> 历史 adapter 可能由旧协议训练，评估时的训练数据来源校验不等于训练编码协议校验，
> 不能将其标为训练协议已对齐；本次没有重新训练或覆盖历史产物。

> **评估快照与候选范围更新**：留出问题仍只来自验证来源，但检索候选改为整个
> `corpus.jsonl`（包括训练文档片段），输出 `eval_report_full_corpus_v2.json`，
> 不覆盖旧报告。报告记录语料、问题、统计、解析排序代码和样例原文的 SHA-256、
> 候选数与留出问题数，并统计跨训练/验证来源的完全相同片段数。完全相同检查不等于
> 近似重复或语义泄漏检查。新版评估将先解析固定的本地模型目录，加载前、加载后
> 和评估结束分别校验模型与 adapter 的内容摘要。缓存目录名中的 revision 只是标签，
> 非 Hugging Face 缓存路径记为 `unknown`；摘要也不能证明进程内权重或外部引用的身份。
> 跨次比较还必须核对模型摘要、依赖、编码协议与数据快照，不能仅凭指标差异声称提升。
> 这次只实现和测试评估流程，**没有重新运行真实模型，也没有新的效果数字**。
> 项目文档验证集使用 `corpus.py` 预分块（500 字符窗、80 重叠）；线上后端
> `knowledge.split_document` 使用 1000 字符窗、150 重叠。查询指令对齐并不代表
> 分块或端到端流程一致，验证集结果不能直接作为线上效果。
> 验证集还只编码预分块的 `row.text`，线上后端则拼接文件名、标题和片段正文。
> 评估报告区分文档 prompt 的显式参数与模型默认值，并记录默认 prompt 名称和摘要。
>
> 另有一次独立执行的本机离线基座冒烟：仓库自带 30 题诊断集与 24 题独立标注问题
> 使用同一 5 文档合成语料，报告位于本机 `.local/rag-eval-runs/`。这次没有加载
> adapter、没有重训或调用生成模型；其内联运行脚本未作为文件保存，报告的源码、
> 输入与模型摘要不能替代完整可执行 harness。结果不能与旧 LoRA 数字直接比较。
> 现已提供可执行的 `eval_examples.py` 用于单独复验这两套仓库合成问题，不能将
> 用旧内联脚本得到的报告误称为由该 runner 生成。

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

### 新实验如何固定输入

为避免覆盖历史实验，请为新语料指定全新目录；若目录已有 `corpus.jsonl`、
`corpus_stats.json` 或 `queries.jsonl`，生成脚本会拒绝覆盖。新问题会记录对应片段
的 SHA-256；如果片段内容后来变化，续跑生成、训练和评估都会在调用模型前拒绝该问题。
语料本身也会按快照摘要检查，重复/缺失 ID 和来源不匹配会被拒绝。

```bash
cd services/zebao-rag
.venv/bin/python scripts/finetune/corpus.py --out-dir /绝对路径/新的实验目录
# 以下生成步骤会调用本机 Ollama，按需单独运行并注意耗时和模型使用：
.venv/bin/python scripts/finetune/generate_queries.py --work-dir /绝对路径/新的实验目录 --split train
.venv/bin/python scripts/finetune/generate_queries.py --work-dir /绝对路径/新的实验目录 --split val
```

旧实验缺少片段内容摘要；默认拒绝把它当成可验证快照。确需复查旧实验时，可为
`train_lora.py` 或 `eval_retrieval.py` 显式添加
`--allow-legacy-queries`；输出中的 `question_provenance` 会标记为
`legacy_unverified`，不能据此证明问题与当时的片段完全一致。旧文件不会被自动改写。
问题生成不支持向未验证的历史问题文件追加，请先创建新的实验目录。
同一冻结快照、相同模型/adapter 和编码协议下重复运行，才适合比较基线与候选。
验证来源、片段或问题为空时默认拒绝评估；如只需诊断集，可以显式使用
`--allow-empty-heldout`，报告将标记 `heldout_evaluable=false`，不能宣称留出集通过。
新训练会记录训练问题与片段组成的摘要；评估时会检查 adapter 中的训练摘要是否与
当前快照一致。旧 adapter 缺少训练摘要时，默认拒绝评估；如只想复查历史实验，
须另加 `--allow-unverified-adapter`，报告会标记 `legacy_unverified`，不能把它当成
已验证的训练来源。已存在 adapter 目录不会被覆盖，可用训练参数 `--adapter-output`
指定新目录；已存在的评估报告也不会覆盖，可用评估参数 `--output` 指定新文件。
新训练元数据会记录随机种子、最大长度、最大步数及依赖版本；训练入口尚未验证实际
基座权重身份，元数据明确标记 `model_artifact_identity_verified=false`，不能据此
宣称训练完整可复现。
评估模型解析现在默认只使用本地模型目录或 Hugging Face 缓存；只有显式添加
`--allow-model-download` 才允许下载缺失的快照。这是运行行为的变更；本次未因该
选项下载模型，也没有重训或覆盖已有评估报告。
身份检查会在加载前、加载后及评估结束多次读取整个模型目录，使用 adapter 时还会在
加载 adapter 后复核；大模型可能明显增加磁盘读取量和耗时，摘要不能替代权重签名。

仅评估仓库的两套合成问题、且不加载 adapter 或训练资料时，可运行：

```bash
cd services/zebao-rag
.venv/bin/python scripts/finetune/eval_examples.py \
  --model /绝对路径/已存在的本地模型快照 \
  --output /绝对路径/此前不存在的报告.json
```

该 runner 默认禁用 Python socket/DNS 与 Hugging Face 联网，输出路径必须全新；
会运行 30 题诊断和 24 题留出问题，两个问题集共用同一合成文档库。它不会调用
生成模型，因此检索负例不代表生成错误或拒答结果。MPS 与磁盘读取可能占用大量
本机资源，运行前应确认其他任务不会与之竞争；Python 层禁止联网并非操作系统级
网络沙箱。

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

评估脚本复用后端分块与排序代码，需要单独安装 RAG 项目的 `eval` 依赖组。
建议从 DevAssist 根目录创建独立评估环境，避免同步操作移除已有服务环境中的其他包：

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.local/rag-eval-venv" \
  uv sync --project services/zebao-rag --frozen --group eval --python 3.12
```

这是完整的独立 Python 环境，首次安装可能需要下载较大的依赖包；安装本身不下载模型权重。
从 `backend/` 运行评估时，将下方命令中的
`../services/zebao-rag/.venv/bin/python` 换成 `../.local/rag-eval-venv/bin/python`。
普通 worker 的默认依赖不包含 `eval` 组。已有服务环境如需保留额外包，
不要直接用同步命令覆盖它；本机已通过非破坏性的离线安装补齐了本次锁定版本。

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

产物：实验目录中的 `eval_report_full_corpus_v2.json`（含逐条失败样例与输入摘要）；
路径与历史 `eval_report.json`、`eval_report_online_protocol_v1.json` 分开。

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
RAG_VECTOR_STORE=chroma      # 可选 Chroma；后端默认 Milvus
```

新版 worker 自动根据模型与 adapter 内容、编码实现和依赖计算协议指纹；后端在读取
Redis 缓存前验证 worker 身份，并用该指纹区分缓存键。`RAG_EMBEDDING_IDENTITY`
保留以兼容旧配置，但**不决定新版 `vector_rank` 缓存键**。旧 worker 缺少身份时不会复用
旧缓存，会返回向量服务不可用；混合检索按已有规则降级。

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
