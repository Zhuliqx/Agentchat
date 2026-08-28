# RAG 质量评估指南

> 相关文档：见 [文档地图](README.md)；项目 2 见 [AGENT_TASK](AGENT_TASK.md)。
> 最后校验：2026-08-29（文档与当前代码同步；防漂移检查见 `backend/scripts/check_docs_stale.py`）

> 目标：把 RAG 质量从"拍脑袋"变成**可量化、可复现、可回归**。
>
> 评估体系三层互补：**命中（MRR/Hit@K）→ 排序（NDCG@K）→ 内容（四指标）**。
> 检索级指标可进 CI 防退化；生成级四指标使用 LLM-as-judge（DeepSeek）做深度调优。
>
> 本指南同时记录**评估体系自身被校准的三轮迭代**——评估工具和被测系统一样
> 需要工程化，这是本项目最值得面试复述的部分。

---

## 目录

- [1. 分层评估体系](#1-分层评估体系)
- [2. 快速上手](#2-快速上手)
- [3. 指标定义](#3-指标定义)
- [4. Ground Truth 标注规范](#4-ground-truth-标注规范)
- [5. LLM-as-judge 设计](#5-llm-as-judge-设计)
- [6. 成本参考](#6-成本参考)
- [7. 当前基线（最终版 40 条）](#7-当前基线最终版-40-条)
- [8. 迭代历程与关键发现](#8-迭代历程与关键发现)
- [9. 经验教训与面试话术](#9-经验教训与面试话术)
- [10. 与其它文档的关系](#10-与其它文档的关系)

---

## 1. 分层评估体系

| 层级 | 脚本 | 指标 | 成本 | 用途 |
|------|------|------|------|------|
| 命中 | `scripts/eval_rag.py`（默认） | **MRR、Hit@1/3/5** | 无 LLM，仅 Milvus+Postgres | 每次改参数/摄入后防退化，可进 CI |
| 排序 | `scripts/eval_rag.py --graded` | **NDCG@1/3/5**（0/1/2 分级） | 每 case 1 次 DeepSeek | 衡量"核心块是否置顶"的排序质量 |
| 内容 | `scripts/eval_quality.py` | **context_precision / context_recall / faithfulness / answer_relevancy** | 每 case ~5 次 DeepSeek | 季度性深度调优 + A/B 报告 |

**三层为什么互补**：
- 命中率 100%（Hit@1=1.0）不代表排序完美——NDCG 能揭示"核心块排第 3/4"的隐藏问题；
- 检索全对不代表回答正确——faithfulness/relevancy 才能反映生成质量；
- 评估必须**复现生产管线**（混合检索 + rerank + 去重合并），否则指标失真（见 §7.3 实证）。

> **与 admin.py 在线评估的分工**：本项目另有管理后台评估（`/api/admin/eval`，路由在
> `app/api/routes/admin.py`、逻辑在 `app/evaluation/kb_eval.py`），定位是**在线连通性体检**——自动从文档提取关键词、
> 判定"每个文档能否被检索召回"，适合日常巡检。本 CLI 评估（`scripts/eval_*` +
> `app/evaluation/`）定位是**离线深度质量评估**——40 条人工 GT + LLM-judge
> 四指标 + NDCG 排序，适合版本迭代/参数调优。二者互补不冲突：在线快检连通性，
> 离线深查质量。

---

## 2. 快速上手

### 2.1 准备：摄入知识库文档（评估前必做）

```powershell
# 1) 启动依赖（Docker Desktop 需先打开）
docker compose up -d

# 2) 初始化数据库（首次）
cd backend
.\venv\Scripts\python.exe scripts/init_db.py

# 3) 摄入真实文档（示例：本项目三篇基准文档）
.\venv\Scripts\python.exe scripts/ingest_docs.py "..\data\kb\company.md"
.\venv\Scripts\python.exe scripts/ingest_docs.py "..\data\uploads\52cac0127fd343daa4a627cba4da434e\knowledge.txt"
.\venv\Scripts\python.exe scripts/ingest_docs.py "..\data\uploads\df88660db5d6435d8b6b6ead2676364d\full.md"
```

> 说明：摄入后文档的 `source` = 绝对路径，`ground_truth.json` 中 `expected_sources`
> 已按本机路径填写；**换机器/换路径后需同步更新 GT**。

### 2.2 跑评估

> **乱码问题**：Windows 控制台默认 GBK，与脚本 UTF-8 输出不一致会导致乱码。
> 建议在 PowerShell 执行一次（永久生效）：
> ```powershell
> [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
> ```
> 或临时 `chcp 65001`。脚本内部已强制 stdout/stderr 为 UTF-8 并兜底 `errors=replace`，
> 即使不设置也不会再崩溃（个别字符可能显示为 �）。

```powershell
# 1) 检索级（命中 + 排序；需 Postgres + Milvus 运行、已摄入文档）
.\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json   # MRR/Hit@K
.\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json --graded   # NDCG@1/3/5

# 2) 端到端四指标（需 DeepSeek key；先试 3 条看成本）
.\venv\Scripts\python.exe scripts/eval_quality.py --max-cases 3
.\venv\Scripts\python.exe scripts/eval_quality.py --out data/eval/run_baseline.json --report

# 3) A/B：改一个参数再跑一次，对比
.\venv\Scripts\python.exe scripts/eval_quality.py --no-rerank --out data/eval/run_no_rerank.json
.\venv\Scripts\python.exe scripts/eval_quality.py --compare data/eval/run_baseline.json data/eval/run_no_rerank.json
```

**A/B 铁律：每次只动一个变量**，否则无法归因。

### 2.3 CLI 参数速查

| 脚本 | 参数 | 说明 |
|------|------|------|
| `eval_rag.py` | `--dataset` | GT json 路径（默认内置案例） |
| | `--top-k` | 检索 top_k |
| | `--graded` | NDCG 分级模式（LLM judge 打 0/1/2） |
| | `--rerank-max-length` | 覆盖 RERANK_MAX_LENGTH（A/B） |
| | `--rewrite` | 查询改写 A/B：覆盖 QUERY_REWRITE_MODE（检索侧 MRR/Hit@K） |
| | `--compare A B` | 对比两次评估 JSON |
| `eval_quality.py` | `--max-cases N` | 只跑前 N 条（试成本） |
| | `--only q28,q31` | 只评估指定案例（调试） |
| | `--skip-generation` | 跳过生成层（只算检索两指标） |
| | `--top-k / --threshold` | 覆盖检索参数 |
| | `--no-rerank / --no-hybrid` | A/B：关闭对应环节 |
| | `--max-per-doc N` | 覆盖 RAG_MAX_PER_DOC（A/B） |
| | `--rewrite` | 查询改写 A/B：覆盖 QUERY_REWRITE_MODE（生成侧四指标） |
| | `--out path / --report` | 保存结果 JSON / Markdown 报告 |
| | `--compare A B` | 对比两份结果 JSON |

---

## 3. 指标定义

### 3.1 检索级：命中与排序

| 指标 | 定义 | 说明 |
|------|------|------|
| **Hit@K** | 正确答案是否出现在前 K 个结果中 | 粗粒度"有没有"；二值相关 |
| **MRR** | 第一个正确答案排名的倒数均值 | 只关心第一个命中位置 |
| **NDCG@K** | `DCG@K / IDCG@K`，`DCG = Σ (2^rel_i - 1)/log2(i+1)` | 需要**分级相关度**（0/1/2）；对"核心块是否置顶"敏感 |

- NDCG 的三档分级：`2=核心直接命中 / 1=部分相关/上下文 / 0=不相关`，由 LLM judge 判定。
- 区分示例：核心块排第 3 时，二值指标（Hit/MRR）可能仍满分，但 NDCG@1 只有 0.33。

### 3.2 生成级四指标（对齐 RAGAS 口径）

| 指标 | 定义 | 低分含义 → 排查方向 |
|------|------|----------------------|
| **context_precision** | 检索块中真正相关的比例（位置加权，靠前加分） | 检索精度差 → rerank 候选/阈值/embedding |
| **context_recall** | 标准答案的关键信息点被检索块覆盖的比例 | 漏召回 → 分块/语料覆盖/top_k |
| **faithfulness** | 答案句子中可由检索块支撑的比例（幻觉越低越高） | 生成幻觉 → prompt 约束/上下文压缩过头 |
| **answer_relevancy** | 答案与问题的相关度（0-5 归一化） | 跑题 → Query 改写/prompt 模板 |

- 检索块获取路径与**生产完全一致**：`app/rag/retriever.py`（混合检索 + rerank + 上下文压缩）。
- 生成层在 `--skip-generation` 时可跳过，只做检索层两指标（更快更便宜）。

### 3.3 三层互补关系

```
命中层:  Hit@K / MRR      "正确答案在不在、在第几位"        ← 便宜、可进 CI
排序层:  NDCG@K           "核心块有没有排在最前"            ← LLM 分级
内容层:  四指标            "检索块/答案内容质量如何"          ← LLM judge
```

---

## 4. Ground Truth 标注规范

文件：`backend/data/eval/ground_truth.json`（被 gitignore，属私有基准）；
公共示例：`backend/tests/fixtures/rag_ground_truth.example.json`（可提交，CI 单测用）；
设计扩充示例：`backend/tests/fixtures/rag_ground_truth.v2.json`（覆盖图片/图文双通道、表格/多栏、文档级去重、口语改写）。

```json
{ "id": "q01",
  "question": "公司有多少名员工？",
  "answer": "公司现有约 120 名员工。",
  "expected_sources": [".../company.md"],
  "notes": "来源：company.md 第3段" }
```

规范：
- **人工标注**，answer 必须来自真实知识库文档（notes 注明来源），**不要用 LLM 自动生成 GT**（会引入系统性偏差）；
- 覆盖类型矩阵（当前 40 条已覆盖全部）：
  - 事实型 / 数字型（q01-q08）
  - 规则/计费型（q09-q23）
  - 预算数字型（q24-q27）
  - **否定型**（q28-q29：库内确实无答案，answer 填"知识库中没有相关信息"——测"承认不知道"）
  - **难例**（q31-q40）：对比型 / 计算型 / 条件推理 / 筛选型 / 状态型——提升评估区分度
- `expected_sources` 供 eval_rag.py 做 source 模式判定（文档级），内容级判定看四指标；
- `expected_images`（可选）供图文双通道判定：格式 `source#image_index`（如 `.../c.md#2`），
  与 `expected_sources` 是"或"关系（命中任一即该 case 命中）；
- `notes` 必填"来源: 文件名@位置"，便于指标异常时回溯定位。

---

## 5. LLM-as-judge 设计

### 5.1 设计要点（`app/evaluation/judge_llm.py`）

- 固定 `deepseek-chat`、**temperature=0**（可复现）、`response_format=json_object`；
- 每个指标一次评审调用返回结构化 JSON（relevant[] / key_points+covered[] / sentences+supported[] / score 0-5 / relevance[]）；
- **faithfulness 逐句判定、context_recall 逐关键点判定**——比"整段打分"更准，也能对抗 judge 高估；
- **输入截断 `_format_docs` 默认 800 字符**——与 `chunk_size=800` 对齐（过小会系统性低估 CR/Faith，见 §8.1）；
- **relevancy 评分指引覆盖对比/筛选/否定型**（见 §8.2），避免"非单一事实句"被误判不相关。

### 5.2 已知局限与缓解

| 局限 | 缓解 |
|------|------|
| judge 与生成同模型可能整体高估 | 只看 **A/B 相对变化**；条件允许时换更强 judge（qwen-max） |
| DeepSeek JSON 偶发不合规 | `jump_to_json` 剥离围栏/杂文 + 重试 1 次 + 失败返回 `{}`（该 case 丢标并在报告中可见） |
| 单条 case 的 LLM 判断有方差 | 报告看 macro 平均；对关键 case 人工复核 |
| 分级/判定本身需校准 | 每次修改 judge 后重跑基线对比（本项目已做三轮，见 §8） |

---

## 6. 成本参考

- **检索级**：`eval_rag.py` 默认模式零 LLM 成本（仅 DB）；
- **排序级**：`--graded` 每 case 1 次 DeepSeek，40 条约 40 次；
- **生成级**：每 case ~5 次 DeepSeek（precision/recall/生成/faithfulness/relevancy）；
- 40 条全量四指标 + 一次 A/B ≈ 400 次 `deepseek-chat` 调用，量级很小；
- 调试期用 `--max-cases 5` 或 `--only q28,q31` 快速试跑。

---

## 7. 当前基线（最终版 40 条）

> 数据源：`company.md`(4块) + `knowledge.txt`(8块) + `full.md`(87块)；
> 40 条 GT（含 10 条难例），DeepSeek judge（temperature=0），rerank 开启，
> `rag_max_per_doc=3`、judge 截断 800。

### 7.1 检索级

| 指标 | 值 |
|------|-----|
| **MRR** | **1.000** |
| **Hit@1** | **1.000**（40/40 全部首位命中来源） |
| **NDCG@1 / @3 / @5** | **0.858 / 0.917 / 0.937** |

> **口径注**：本节 MRR/Hit@1 = 1.000 是"来源级 40/40 全部首位命中"的某一轮快照；
> 与 §7.2b 及 README 最终基线 **0.963 / 0.925**（来源级，另一轮/口径）不同——
> 以 README 标注的最终基线为准，详见 [RAG_OPTIMIZATION](RAG_OPTIMIZATION.md) §6。

> 关键解读：来源级命中 100%，但内容级排序 NDCG@1=0.858——**33/40 条首块即核心，
> 7 条核心块排在 2~4 位**。这是 MRR/Hit@K 完全不可见的一维（详见 §7.3）。

### 7.2 生成级四指标

| 指标 | 值 |
|------|-----|
| context_precision | **0.925** |
| context_recall | **0.963** |
| faithfulness | **0.923** |
| answer_relevancy | **1.000**（40/40） |

> 重跑（40 条，`eval_quality.py` 默认配置）。
> 口语集（8 条口语化变体）改写 A/B 端到端验证：rewrite rule/llm 均让 faithfulness 微降
> （0.975 → 0.863/0.853），检索指标不变——结论见 [EXPERIMENTS.md](EXPERIMENTS.md) §1。

### 7.2b RAG 优化 A/B（自适应检索 / 意图路由 / 去重 / 预算）

> 本轮新增的检索侧优化全部默认关（**行为不变**），跨书面集(40) / 口语集(8) 共 6 档评估：

| 档 | 书面 MRR/Hit@1 | 口语 MRR/Hit@1 |
|----|----------------|----------------|
| off（基线） | **0.963 / 0.925** | **0.938 / 0.875** |
| adaptive | 0.963 / 0.925 | 0.938 / 0.875 |
| adaptive+rule 改写 | 0.958 / 0.925 | 0.938 / 0.875 |
| intent | 0.963 / 0.925 | 0.938 / 0.875 |
| dedup+预算 3000 | 0.963 / 0.925 | — |

> **零回退**；书面集已饱和故无显著增益。**决策：维持默认关**，后续用更大口语 GT / 真实 query 复测。
> 参数与结论详见 `docs/RAG_OPTIMIZATION.md` §6。

### 7.3 消融实验：每一层检索增强的价值

| 档位 | 检索通道 | MRR | Hit@1 | Hit@3 | CP | CR |
|------|---------|-----|-------|-------|-----|-----|
| A 纯向量 | 仅 Milvus | 0.963 | 0.950 | 0.975 | 0.917 | 0.894 |
| B 混合检索 | 向量+BM25+RRF | 0.975 | 0.950 | 1.000 | 0.910 | 0.931 |
| C 混合+rerank | +CrossEncoder | 0.944 | 0.900 | 0.975 | 0.925 | 0.963 |

**解读（三层维度互补的实证）**：
- **混合检索（BM25+RRF）**：MRR 0.963→0.975、CR 0.894→0.931（+3.7pp）——关键词通道补召回的收益；
- **rerank**：CP 0.910→0.925、CR 0.931→0.963（+3.2pp）——内容级排序/覆盖收益；
- **来源级 MRR/Hit@1 微降**（0.975/0.950 → 0.944/0.900）：rerank 优化块级内容排序，偶把「来源命中的块」重排到 top-k 边缘——**来源级与内容级指标方向可能相反**，正因如此需要三层互补（§1）。

复现：`eval_rag.py --no-hybrid --no-rerank`（A）→ `--no-rerank`（B）→ 默认（C）；`eval_quality.py --skip-generation` 取 CP/CR。

> Agent 编排质量评估（路由/完成/拒绝）见 `docs/AGENT_EVAL.md`。

### 7.4 Embedding 选型（对比结果）

- **方法** `scripts/eval_embedding.py`：同一 GT + 相同块文本，进程内暴力检索（≈ HNSW 上界），
  来源级 Hit@1/3 + MRR；`--models` 多模型、`--pooling {cls,mean}`、统一 transformers 加载 + L2 归一化；
- **对比**：

  | 模型 | 维度 | 来源 | Hit@1 | Hit@3 | MRR |
  |------|------|------|-------|-------|-----|
  | **bge-small-zh-v1.5（现用）** | 512 | HF 官方 | **0.975** | **1.000** | **0.988** |
  | bge-base-zh-v1.5 | 768 | HF 官方 | **0.975** | **1.000** | **0.988** |
  | bge-large-zh-v1.5 | 1024 | HF 官方 | 0.950 | 1.000 | 0.975 |
  | m3e-large | 1024 | ModelScope | 0.925 | 0.975 | 0.956 |

- **结论**：现用 **bge-small-zh 是最优选择**（与 bge-base 并列，且维度最小 → 推理/内存最省）；
  **「更大不一定更好」实证**：bge-large(1024) 略降、m3e-large 明显落后——选型必须用自建 GT 验证；
  （缺 pooling config，检索质量 0.15），官方补齐后正常（0.975）——**模型必须完整下载 + 用评估验证**；
- 复现：`python scripts/eval_embedding.py --models "BAAI/bge-small-zh-v1.5,BAAI/bge-base-zh-v1.5"`。

### 7.5 排序优化 A/B 记录（NDCG 驱动）

| 轮次 | 变更 | NDCG@1 | 结论 |
|------|------|--------|------|
| --graded 用原始 `hybrid.search_hybrid` | 0.825 | **评估路径错误**：生产走 rerank+去重，原始排序低估系统 |
| 改为生产同路径 retriever | 0.858（+3.3pp） | rerank 确实修正了部分排序（q05 核心块 #2→#1） |
| `rerank_max_length` 512→800 | 0.842（-1.6pp） | **512 已最优**，排除"截断导致排序差"假设 |

**剩余未置顶案例归因**（~7 条）：
- 否定型（q28：文档确实无该信息）→ 非排序问题；
- 跨文档归纳（q30）→ 受 `rag_max_per_doc=3` 同文档限流影响；
- 复合条件/数字型（q03/q37/q39）→ 核心块被"含关键词但非核心"的问答口径块压过，
  属 rerank 真实短板，进一步优化需换更强 rerank 模型或 Query 改写（成本更高，暂缓）。

---

## 8. 迭代历程与关键发现

> 三轮自我校准（judge 截断 400→800、relevancy 口径、--graded 生产同路径）、q11 案例复盘、
> 生成策略 v4→v7 迭代的**完整记录已移至 [EXPERIMENTS.md](EXPERIMENTS.md) §4**。
> 保留要点：校准一 CR +11.5pp / Faith +8.2pp；校准二 Rel +11.5pp；校准三 NDCG@1 +3.3pp。

---

## 9. 经验教训与面试话术

### 9.1 工程经验清单

1. **评估必须复现生产管线**——原始混合排序 ≠ 真实系统输出（rerank/去重/限流都会改变结果），否则指标失真；
2. **评估工具本身也要校准**——judge 输入截断、评分口径都会系统性扭曲指标，修改后必须重跑基线；
3. **命中率 100% ≠ 排序完美**——需要 Hit@K（命中）+ NDCG（排序）+ 四指标（内容）三层互补；
4. **A/B 铁律：一次只动一个变量**——每个参数变更都要有归因（rerank 开关 / max_per_doc / judge 截断 / 评分口径，均为独立 A/B）；
5. **source 模式是"文档级"判定**，会掩盖"同文档错块"漏召回——内容级判定必须看 CP/CR；
6. **否定型/对比/筛选型答案需要专门的评分指引**，否则 judge 系统性低估。

### 9.2 面试可直接复述的话术

> 已移至 [interview/evaluation.md](interview/evaluation.md)（面试素材）。

### 9.3 关键数字速查

| 指标 | 值 |
|------|-----|
| 检索 Hit@1 / MRR | 1.000 / 1.000 |
| NDCG@1 / @3 / @5 | 0.858 / 0.917 / 0.937 |
| 四指标 CP / CR / Faith / Rel | 0.904 / 0.958 / 0.940 / 1.000 |
| 校准一（judge 截断） | CR +11.5pp、Faith +8.2pp |
| 校准二（relevancy 口径） | Rel +11.5pp（0.885→1.0） |

> 上表为**早期基线快照**（v4，2026-08 上旬）；最终基线见 §7.2（CP 0.925 / CR 0.963 / Faith 0.923 / Rel 1.0）。

---

## 10. 与其它文档的关系

- 失败模式与排查方法论：`docs/RAG_DESIGN_ANALYSIS.md` 第 13/15 节
- 检索/rerank 实现细节：`backend/app/rag/*`（见 `docs/DEEP_DIVE.md`）
- 架构总览：`docs/ARCHITECTURE.md`、`docs/EXPLAIN.md`
- 上线可观测性（Langfuse）：`docs/OBSERVABILITY.md`（接入后补充）
- 评估代码入口：`backend/app/evaluation/`、`backend/scripts/eval_{rag,quality}.py`

---

_本文档结合代码与真实评估数据整理；代码/参数演进后请同步更新。_
