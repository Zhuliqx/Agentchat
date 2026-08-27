# RAG 优化方案（自适应检索 / 意图路由 / 去重 / 预算 / CI 回归）

> 目标：在不破坏现有检索质量、且**默认全部关闭（行为不变）**的前提下，提供一整套可开启、可 A/B 评估的 RAG 优化能力，延续"数据驱动决策"的风格（类似查询改写的"默认关、实验证明后再开"）。

---

## 1. 优化点总览

| # | 优化 | 开关（默认关=不变） | 作用 | 成本 |
|---|------|-------------------|------|------|
| **自适应检索**（低置信触发改写 + 放宽候选） | `adaptive_retrieval` | 口语/低召回 query 才走"改写双路 + 放宽 rerank 候选"，正式 query 零额外成本 | 低 |
| **检索级意图路由** | `intent_routing` | 按 query 类型（fact/chat/list/compare）调策略：改写开不开、top_k、多子查询 | 低 |
| **跨块去重 + 总预算** | `dedup_near_duplicate` / `rag_max_total_chars` | 指纹/语义去重 + 总字符预算，减少喂给 LLM 的冗余 | 低 |
| **CI 完整 GT 回归** | ci.yml 一步 | 用 `eval_rag.py` 跑 GT MRR 防退化（非阻塞） | 低 |
| **图片语义描述（VLM）** | `image_vlm_enabled` | 对图片/图表用视觉大模型生成文本描述入文本通道——图内容（趋势/结构/示意图逻辑）可检索 | 中（每图一次 VLM 推理） |
| **图文双通道** | `image_dual_channel` | 图片用多模态向量（独立 collection）索引，检索时与文本通道融合——像素级/“看一眼像”相似召回 | 中（多模态 embedding + 图向量 collection） |
| **PDF 提取回退** | 恒启用 | PDF 文本用 `pdfplumber→pymupdf→pypdf` 逐级回退，中文/表格保留更好、控制字符更少 | 低 |
| **Markdown 去标题** | `markdown_strip_headers=True`（已默认开） | 标题只存 metadata、不进正文，块更聚焦；实测 MRR 0.963→0.975 | 低 |

---

## 2. 配置项（`backend/app/config.py`）

```python
# ---- 自适应检索（RAG 优化）----
adaptive_retrieval: bool = False   # 低置信触发改写双路 + 放宽候选（默认关=行为不变）
conf_trigger_threshold: float = 0.45  # 初检索最高分低于此值 → 判"可能召不全"触发自适应
adaptive_candidate_k: int = 9      # 自适应放宽后的 rerank 候选（默认≈6×1.5）
intent_routing: bool = False       # 检索级意图路由
dedup_near_duplicate: bool = False # 跨块/跨源 指纹+语义 去重（默认关=行为不变）
dedup_sim_threshold: float = 0.90
rag_max_total_chars: int = 0       # 0=不限制；>0 按分数降序累计截断，防超长 context

# ---- 图片语义描述（VLM）----
image_vlm_enabled: bool = False
image_vlm_provider: str = "deepseek"     # deepseek | dashscope | openai | ollama
image_vlm_model: str = "deepseek-v4-flash-vision-exp"
image_vlm_max_size: int = 1280           # 送入 VLM 前最长边缩放到此值
image_vlm_detail: str = "low"            # low | high | original | auto
# ---- 图文双通道----
image_dual_channel: bool = False         # 需下载多模态模型（见 SETUP）
image_embedding_provider: str = "local"
image_embedding_model: str = "OFA-Sys/chinese-clip-vit-base-patch16"
image_embedding_dim: int = 512
image_channel_top_k: int = 6
image_channel_weight: float = 0.4
# ---- 解析增强----
markdown_strip_headers: bool = True      # Markdown 去标题（默认开）
# PDF 文本提取使用 pdfplumber→pymupdf→pypdf 逐级回退
```

> 全部**默认关**，现有检索行为零变化；评估通过后再按数据决定开启哪项。

---

## 3. 各优化点详细说明

### 3.1 自适应检索（低置信触发）
- **文件**：`backend/app/rag/retriever.py`（`_get_relevant_documents` / `_search_queries` / `_best_score`）
- **流程**：先单路初检索 → `_best_score`（rerank/rrf/向量最高分）→ 低于 `conf_trigger_threshold` → 判定"可能召不全" → 用 `adaptive_candidate_k` 放宽候选 + `_expand_queries` 改写双路**二次检索**合并；否则直接收尾（**零额外成本**）。
- **为什么**：正式书面 query 基线已饱和（`EVALUATION_REPORT`），只有"口语/低召回"才值得为改写付出 LLM/双路成本。

### 3.2 检索级意图路由
- **文件**：`backend/app/rag/intent.py`（`classify` / `split_compare`，规则版零 LLM）
- **分类**：`fact`（默认）/ `chat`（口语）/ `list`（列举）/ `compare`（对比）
- **策略**：
  - `chat` → 用 `adaptive_candidate_k` 放宽候选（并配合改写）；
  - `list` → `top_k` 放大到≥6、`score_threshold` 调低；
  - `compare` → 按 `和|与|vs` 拆多子查询分别检索再 `_merge_multi` 合并；
  - `fact` → 走默认（不改写、默认 top_k）。

### 3.3 跨块去重 + 总预算
- **文件**：`retriever.py`（`_dedupe_near_duplicate` / `_apply_total_budget` / `_cosine`）
- **指纹去重**：`_normalize_text`（去空白/标点/小写）后相同文本只留最高分（跨源也适用，零成本）；
- **语义去重**：`dedup_near_duplicate=True` 时，对指纹去重后用 `get_embedder().embed_texts` 求余弦，相似≥`0.90` 只留分数高者（失败自动降级为仅指纹去重）；
- **总预算**：`rag_max_total_chars` >0 时按分数降序累计到预算即截断，防超长 context。

### 3.4 CI 完整 GT 回归
- **文件**：`.github/workflows/ci.yml`（`rag-regression` job 内新增一步）
- 在现有 "hit-rate threshold" 后，加"full GT MRR, non-blocking"：`python scripts/eval_rag.py --dataset tests/fixtures/rag_ground_truth.example.json`，`continue-on-error: true` 防阻塞。

---

## 4. A/B 评估方法（延续查询改写"数据驱动"）

- **测试集**：
  - 书面：`data/eval/ground_truth.json`（40 条，MRR 0.944 / Hit@1 0.900）；
  - 口语：`data/eval/ground_truth_spoken.json`（8 条，命中"改写目标场景"）。
- **对比档**：`off`（基线）vs `adaptive`（自适应）vs `intent`（意图路由）vs `dedup`（去重+预算）。
- **复现命令**：
  ```powershell
  # 基线(off)
  .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  # 自适应
  $env:ADAPTIVE_RETRIEVAL="true"; .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  # 口语集 + 端到端四指标
  .\venv\Scripts\python.exe scripts/eval_quality.py --dataset data/eval/ground_truth_spoken.json --rewrite rule
  ```
- **看什么**：MRR / Hit@1（召回层）与 faithfulness（生成层）是否提升；再算 **token 用量**（`rag_max_total_chars` / 去重是否减少输入）。

> ⚠️ **重要**：RAG 优化各开关**默认全关**，需在真实/自建 GT 上 A/B，**用数据决定**是否开启及阈值（同查询改写"默认关"的决策）。**没有 A/B 结论前，不要在生产开启。**

---

## 5. 单元测试
- `tests/unit/test_rag_optimization.py`：意图分类 / 拆分 / 指纹去重 / 语义去重降级 / 总预算截断 / 文本归一化 / 余弦。
- 全部 mock，不依赖 Milvus/Postgres/embedding 模型。

## 6. A/B 实测结果（2026-08-25，Postgres+Milvus 在线，本地 bge-small-zh / rerank）

**书面集 `ground_truth.json`（40 条，`expected_sources` 命中）**

| 档 | 开关 | MRR | Hit@1 | vs off |
|----|------|-----|-------|--------|
| off（基线） | 全关 | **0.963** | **0.925** | — |
| adaptive | `ADAPTIVE_RETRIEVAL=true` | 0.963 | 0.925 | 0 / 0 |
| adaptive + rule 改写 | `+QUERY_REWRITE_ENABLED=true, MODE=rule` | 0.958 | 0.925 | -0.005 / 0 |
| intent | `INTENT_ROUTING=true` | 0.963 | 0.925 | 0 / 0 |
| dedup + 预算 | `DEDUP_NEAR_DUPLICATE=true, RAG_MAX_TOTAL_CHARS=3000` | 0.963 | 0.925 | 0 / 0 |

**图片 / 解析专项（含图 GT，来源级命中）**

| 档 | 开关 | MRR | Hit@1 | 结论 |
|----|------|-----|-------|------|
| 图片语义 off → on | `IMAGE_VLM_ENABLED` false→true | 0.000 → **1.000** | 0.000 → **1.000** | 图内容从“完全不可检索”→ 全命中 |
| 图文双通道 off → on | `IMAGE_DUAL_CHANNEL` false→true | 0.000 → **0.750** | 0.000 → **0.667** (Hit@5=1.0) | 纯图文档由图向量召回；图像保底防弱 caption 剔除 |
| strip_headers 关→开 | `MARKDOWN_STRIP_HEADERS` false→true | 0.963 → **0.975** | 0.925 → **0.950** | 已默认开 |
| chunk_size | 500 / 800 / 1200 | 0.963 | 0.925 | 来源级饱和，三者持平；保持 800 |

**口语集 `ground_truth_spoken.json`（8 条，改写目标场景）**

| 档 | 开关 | MRR | Hit@1 | vs off |
|----|------|-----|-------|--------|
| off（基线） | 全关 | **0.938** | **0.875** | — |
| adaptive + rule 改写 | `ADAPTIVE_RETRIEVAL=true` + 改写规则 | 0.938 | 0.875 | 0 / 0 |
| intent | `INTENT_ROUTING=true` | 0.938 | 0.875 | 0 / 0 |

### 读取结论
- **书面集已饱和**（MRR 0.963 / Hit@1 0.925），5 档 RAG 优化全部**零回退**；规则改写对规范书面 query 无增益（MRR 微降 0.005 属噪声）。
- **口语集 8 条**：自适应+改写与 intent 均与基线持平（0.938/0.875），**无回退**（样本仅 8 条，规则改写对低置信句子的提升在 MRR 层面不可见）。
- **决策**：现有 GT 不足以证明任一开关有显著收益，因此**维持默认关、行为不变**（与查询改写"默认关、数据驱动启用"一致）。后续用**更大口径口语 GT 或线上真实 query** 复测，决定是否开启 `adaptive_retrieval` / `intent_routing` / `dedup_near_duplicate` 及其阈值。

### 复现命令（A/B）
```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
.\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json          # off
$env:ADAPTIVE_RETRIEVAL="true"; .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
$env:INTENT_ROUTING="true";     .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
$env:DEDUP_NEAR_DUPLICATE="true"; $env:RAG_MAX_TOTAL_CHARS="3000"; .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
```
生成结果存 `data/eval/rag_eval_*.json`（评估+时间戳）。

---

## 7. 补充 A/B（2026-08-27：检索级饱和确认 + 图片图片语义描述与图文双通道融合实测）

> 在既有 40 书面 + 8 口语 + 10 难例(q31-q40) 上，**来源级检索已完全饱和**（Hit@3/Hit@5≈100%），
> 因此任何"检索级 MRR/Hit"调参均无区分度。真正能区分的是**内容级/图片命中**。

### 7.1 检索级三档：零差异（维持默认）

| 变量 | 取值(书面集) | 结果 | 结论 |
|------|------------|------|------|
| `bm25_use_jieba` | off / on | 0.975 / 0.950 持平 | 默认关 |
| `rag_max_per_doc` | 2 / 3 / 4 | 全 0.975 / 0.950 | 维持 3 |
| `rag_score_threshold` | 0.25 / 0.35 / 0.45 | 全 0.975 / 0.950 | 维持 0.35 |

- 口语集 off/on jieba、max_per_doc、threshold 均持平（口语集 1.000）。
- **难例子集 `ground_truth_hard.json`（q31-q40，10 条）**：off / adaptive / intent / dedup 均 **1.000**——连"难例"来源级也 rank1 命中。

> 结论：当前小知识库来源级检索饱和，检索级参数无法由 MRR 区分；继续维持默认，不做无依据改动。

### 7.2 图片 图片语义描述与图文双通道 融合实测（含图 GT `img_ground_truth.json`，4 条纯图问答）

| 配置 | MRR | Hit@1 | Hit@3 | Hit@5 | 说明 |
|------|-----|-------|-------|-------|------|
| 图文双通道（无图片语义描述） | 0.750 | 0.750 | 0.750 | 0.750 | 图向量+保底；"走势"类(img03)靠图向量弱 |
| 图片语义描述+图文双通道（VLM 描述 + 图向量） | 0.583 | 0.250 | **1.000** | **1.000** | 召回满(救回 img03)，但首名被 VLM 块占用 |
| **图片语义描述+图文双通道 + guard 排序调和** | **1.000** | **1.000** | **1.000** | **1.000** | 图片强制前置 + 移除同位置 VLM 块 → 全 rank1 |

- **VLM 用法**：`deepseek-v4-flash-vision-exp` 官方支持图像输入；OpenAI 兼容 Chat Completions，`image_url` **支持可选 `detail`**(low/high/original/auto)。

### 7.3 决策
- 图片能力推荐 **图片语义描述+图文双通道 同时开启 + guard 排序调和**（`image_dual_channel` + `image_vlm_enabled` 均显式开启，见 SETUP）：当前含图 GT 达 **1.000**，优于仅图文双通道（0.750）。
