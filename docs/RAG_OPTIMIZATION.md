# RAG 优化方案（自适应检索 / 意图路由 / 去重 / 预算 / CI 回归）

> 最后校验：2026-08-29（文档与当前代码同步；防漂移检查见 `backend/scripts/check_docs_stale.py`）
> A/B 实测数据见 [EXPERIMENTS.md](EXPERIMENTS.md) §2-§3；当前基线以 [docs/README.md](README.md) 为准。

> 目标：在不破坏现有检索质量、且**默认全部关闭（行为不变）**的前提下，提供一整套可开启、可 A/B 评估的 RAG 优化能力，延续"数据驱动决策"的风格（类似查询改写的"默认关、实验证明后再开"）。

## 1. 优化点总览

| # | 优化 | 开关（默认关=不变） | 作用 | 成本 |
|---|------|-------------------|------|------|
| **自适应检索**（低置信触发改写 + 放宽候选） | `adaptive_retrieval` | 口语/低召回 query 才走"改写双路 + 放宽 rerank 候选"，正式 query 零额外成本 | 低 |
| **检索级意图路由** | `intent_routing` | 按 query 类型（fact/chat/list/compare）调策略：改写开不开、top_k、多子查询 | 低 |
| **跨块去重 + 总预算** | `dedup_near_duplicate` / `rag_max_total_chars` | 指纹/语义去重 + 总字符预算，减少喂给 LLM 的冗余 | 低 |
| **CI 完整 GT 回归** | ci.yml 一步 | 用 `eval_rag.py` 跑 GT MRR 防退化（非阻塞） | 低 |
| **图片语义描述（VLM）** | `image_vlm_enabled` | 对图片/图表用视觉大模型生成文本描述入文本通道——图内容（趋势/结构/示意图逻辑）可检索 | 中（每图一次 VLM 推理） |
| **图文双通道** | `image_dual_channel` | 图片用多模态向量（独立 collection）索引，检索时与文本通道融合——像素级/"看一眼像"相似召回 | 中（多模态 embedding + 图向量 collection） |
| **PDF 提取回退** | 恒启用 | PDF 文本用 `pdfplumber→pymupdf→pypdf` 逐级回退，中文/表格保留更好、控制字符更少 | 低 |
| **Markdown 去标题** | `markdown_strip_headers=True`（已默认开） | 标题只存 metadata、不进正文，块更聚焦 | 低 |

## 2. 配置项（字段声明在 `backend/app/config_sections.py`，经 `config.py` 聚合）

```python
# ---- 检索增强（默认关；见 EXPERIMENTS.md §3 的 A/B 结论）----
adaptive_retrieval: bool = False   # 低置信触发改写双路 + 放宽候选
conf_trigger_threshold: float = 0.45  # 初检索最高分低于此值 → 判"可能召不全"触发自适应
adaptive_candidate_k: int = 9      # 自适应放宽后的 rerank 候选
intent_routing: bool = False       # 检索级意图路由
dedup_near_duplicate: bool = False # 跨块/跨源 指纹+语义 去重
dedup_sim_threshold: float = 0.90
rag_max_total_chars: int = 0       # 0=不限制；>0 按分数降序累计截断

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
```

## 3. 各优化点详细说明

### 3.1 自适应检索（低置信触发）
- **文件**：`backend/app/rag/retriever.py`（`_get_relevant_documents` / `_search_queries`）；置信信号 `_best_score` 在 `app/rag/postprocess.py`
- **流程**：先单路初检索 → `_best_score`（rerank/rrf/向量最高分）→ 低于 `conf_trigger_threshold` → 判定"可能召不全" → 用 `adaptive_candidate_k` 放宽候选 + `_expand_queries` 改写双路**二次检索**合并；否则直接收尾（**零额外成本**）。
- **为什么**：正式书面 query 基线已饱和，只有"口语/低召回"才值得为改写付出 LLM/双路成本。

### 3.2 检索级意图路由
- **文件**：`backend/app/rag/intent.py`（`classify` / `split_compare`，规则版零 LLM）
- **分类**：`fact`（默认）/ `chat`（口语）/ `list`（列举）/ `compare`（对比）
- **策略**：
  - `chat` → 用 `adaptive_candidate_k` 放宽候选（并配合改写）；
  - `list` → `top_k` 放大到≥6、`score_threshold` 调低；
  - `compare` → 按 `和|与|vs` 拆多子查询分别检索再 `_merge_multi` 合并；
  - `fact` → 走默认（不改写、默认 top_k）。

### 3.3 跨块去重 + 总预算
- **文件**：`app/rag/postprocess.py`（`_dedupe_near_duplicate` / `_apply_total_budget` / `_cosine` / `_normalize_text`）
- **指纹去重**：`_normalize_text`（去空白/标点/小写）后相同文本只留最高分（跨源也适用，零成本）；
- **语义去重**：`dedup_near_duplicate=True` 时，对指纹去重后用 `get_embedder().embed_texts` 求余弦，相似≥`0.90` 只留分数高者（失败自动降级为仅指纹去重）；
- **总预算**：`rag_max_total_chars` >0 时按分数降序累计到预算即截断，防超长 context。

### 3.4 CI 完整 GT 回归
- **文件**：`.github/workflows/ci.yml`（`rag-regression` job）
- 在 "hit-rate threshold" 后，加"full GT MRR, non-blocking"：`python scripts/eval_rag.py --dataset tests/fixtures/rag_ground_truth.example.json`，`continue-on-error: true` 防阻塞。

## 4. A/B 评估方法（延续查询改写"数据驱动"）

- **测试集**：书面 `data/eval/ground_truth.json`（40 条）、口语 `data/eval/ground_truth_spoken.json`（8 条）、扩样 `*_large.json`、扩库 `ground_truth_expand.json`；
- **对比档**：`off`（基线）vs `adaptive` vs `intent` vs `dedup`；
- **复现命令**：
  ```powershell
  cd backend
  .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  $env:ADAPTIVE_RETRIEVAL="true"; .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  $env:INTENT_ROUTING="true";     .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  $env:DEDUP_NEAR_DUPLICATE="true"; $env:RAG_MAX_TOTAL_CHARS="3000"; .\venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/ground_truth.json
  ```
- **看什么**：MRR / Hit@1（召回层）与 faithfulness（生成层）是否提升；再算 **token 用量**（`rag_max_total_chars` / 去重是否减少输入）。

> ⚠️ **重要**：RAG 优化各开关**默认全关**，需在真实/自建 GT 上 A/B，**用数据决定**是否开启及阈值。**没有 A/B 结论前，不要在生产开启。**

## 5. 单元测试

- `tests/unit/test_rag_optimization.py`：意图分类 / 拆分 / 指纹去重 / 语义去重降级 / 总预算截断 / 文本归一化 / 余弦。
- 全部 mock，不依赖 Milvus/Postgres/embedding 模型。

## 6. A/B 实测结论（2026-08）

> 完整数据表（书面/口语/难例/扩库四指标 + 超参 sweep + 图片专项）见 [EXPERIMENTS.md](EXPERIMENTS.md) §2-§3。

- **检索级**：书面 40（0.963/0.925）与口语 8（0.938/0.875）上，adaptive / intent / dedup 全部持平或微降；扩样与扩库后三档检索级仍零差异（来源级饱和）。
- **内容级**：三档在难例/口语/扩库上**一致伤 faithfulness**（intent 最明显，adaptive 次之，dedup 几乎不伤），只小幅提升 recall/precision。
- **超参**：`rrf_k` × `hybrid_candidate_k` sweep 零区分 → 维持默认 `rrf_k=60`、`hybrid_candidate_k=20`。
- **图片专项**：VLM on → MRR 1.000；图文双通道 on → 0.750；**VLM + 图文双通道 + guard 调和 → 1.000**（推荐同开）。
- **决策**：RAG 三档（adaptive / intent / dedup）**维持默认关、行为不变**；图片能力按需开启。
