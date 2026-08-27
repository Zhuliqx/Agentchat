# RAG 实验报告（查询改写 · 图片语义描述 · 图文双通道）

> 相关文档：[README](../README.md) · [架构文档地图](ARCHITECTURE.md) · [项目2·自主任务Agent](AGENT_TASK.md)

> 目的：验证「查询改写」能否提升检索质量（口语 query × 书面文档的语义鸿沟）。
> 结论先行：**当前知识库基线已饱和，改写无增益，默认关闭**；但基础设施（两档改写、
> 双路兜底、防退化、A/B 评估）已就绪，且实验过程产出了 3 个重要的工程结论。

---

## 1. 方案与落地

| 模块 | 说明 |
|------|------|
| `app/rag/query_rewrite.py` | 两档改写：`rule`（去口语框架词/句尾疑问词 + 同义词**并列扩展**，零依赖）、`llm`（one-shot prompt 改写为精炼检索词，失败/拒绝模板自动回退） |
| `app/rag/retriever.py` | `_expand_queries` 双路检索（原 query + 改写结果），按 `(source, chunk_index)` 合并去重 |
| 防退化 | 精确词豁免：含数字/型号/英文专名跳过 llm 档；拒绝词回退：LLM 输出"请提供问题"等模板即回退原句；双路兜底：改写丢信息时原句仍能召回 |
| 评估 | `scripts/eval_rag.py` 增 `--rewrite {none,rule,llm}`；MRR 主模式改走生产同路径 retriever（顺带统一了此前 graded/非 graded 两条路径） |
| 配置 | `query_rewrite_enabled=False`（默认关）、`query_rewrite_mode`、`query_rewrite_cache_size` |

## 2. 测试集与基线

| 测试集 | 条数 | 特点 | 基线 MRR（改写关，含 rerank） |
|--------|------|------|------|
| `data/eval/ground_truth.json`（全量） | 40 | 规范书面语，按 `expected_sources` 判定 | **0.944**（Hit@1=0.900） |
| `data/eval/ground_truth_spoken.json`（新增） | 8 | 同一批文档的口语化变体，命中"改写目标场景" | **0.938**（Hit@1=0.875） |

## 3. 实验结果

| 配置 | GT 全量 MRR | 口语集 MRR | 结论 |
|------|-------------|-----------|------|
| 基线（改写关） | 0.944 | 0.938 | — |
| `--rewrite rule` | **0.944**（持平） | **0.938**（持平） | 零降损；改写仅做"删口语+并列同义词"，不改变规范 query |
| `--rewrite llm` | **0.944**（持平） | **0.938**（持平） | 零降损；改写质量达标但不影响指标 |

llm 改写质量示例（one-shot prompt 后）：

```
帮我看看公司啥时候成立的     → 公司成立年份
公司现在一共有几个员工啊     → 公司员工人数
基础版一个月多少钱啊        → 基础版 价格 费用
要是到期了数据还能留多久     → 数据到期 保留期限 数据留存时间
```

## 4. 为什么无增益：天花板效应

- 测试集召回已饱和（Hit@3/Hit@5 = 100%），排序由 rerank 主导且稳定；
- 改写只影响"召回"，而这份 3 篇文档的小知识库里，混合检索（BM25+向量+RRF）本就命中，
  **改写的价值窗口被天花板吸收**；
- 改写的真实价值场景是**大规模/高噪声知识库、基线检索差**（口语→书面语义鸿沟时）
  ——当前数据规模不足以支撑该收益。

## 5. 三个重要工程发现（实验过程中的设计修正）

### 5.1 对话式 prompt 会被模型当聊天
初版 prompt（"你是改写助手…问题: xxx"）被 DeepSeek 当成对话，输出
`好的，请提供您需要改写的问题。`。修复：**SystemMessage + one-shot 示例 + 拒绝词回退**
（`_REFUSAL_MARKS`）。修复后输出质量全部达标。

### 5.2 跨路 rerank 分数不可比 → 不能分别精排
尝试"双路各自 rerank 再合并取最优"：MRR 从 0.938 降至 0.917。
原因：rerank 打分是"与各自 query 的相关性"，**不同 query 的分数没有可比性**，
改写路的高分候选可能盖过原路的正确答案。正确做法：**只合并召回、统一精排一次**。

### 5.3 短改写 query 精排不稳 → 精排固定用原 query
尝试"用改写结果精排"（书面语更贴近文档）：`帮我看看公司啥时候成立的` 从 rank1 → rank2。
原因：bge-reranker 对精炼短句（"公司成立年份"）的打分弱于口语原句——
原句含完整实体（公司/成立/哪一年），与文档"公司成立于 2020 年"对齐更强。
最终设计：**改写只承担扩召回，精排始终用原 query**。

## 6. 决策与扩展位

- **默认关闭** `query_rewrite_enabled=False`：当前场景无增益且有 LLM 成本，符合"够用就好"的工程原则；
- 保留扩展位：`query_rewrite_mode` 支持 `none/rule/llm`，`multi`（多路改写取并集）预留；
- **触发式启用**（推荐上线路径）：在"检索兜底/低召回时"条件触发 llm 改写，
  而非全量——既能救回语义鸿沟查询，又不为每次检索付 LLM 成本。

## 7. 复现命令

```bash
# 基线（A）
python scripts/eval_rag.py --dataset data/eval/ground_truth.json
# 实验（B）rule / llm
python scripts/eval_rag.py --dataset data/eval/ground_truth.json --rewrite rule
python scripts/eval_rag.py --dataset data/eval/ground_truth.json --rewrite llm
# 对比（按 query 对齐，含胜/负/平统计 + Hit@K 变化 + 改写对照）
python scripts/eval_rag.py --compare data/eval/rag_eval_A.json data/eval/rag_eval_B.json
```

---

_结论：改写的"正确性"已通过实验验证（两档零降损、llm 质量达标），但"有效性"受限于
当前测试集的天花板。若后续知识库规模化或检索基线变差，启用路径已就绪。_

## 8. 端到端（生成侧）验证（补充）

检索侧无增益后，补跑**生成侧**验证：同一口语集 8 条，rewrite off/rule/llm 三档端到端
（`eval_quality.py` LLM-judge 四指标，生产同路径 retriever）。

| 配置 | CP | CR | **faithfulness** | relevancy |
|------|-----|-----|------|-----------|
| off（基线） | 0.854 | 0.875 | **0.975** | 1.0 |
| rule | 0.854 | 0.875 | **0.863**（-0.112） | 1.0 |
| llm | 0.854 | 0.875 | **0.853**（-0.122） | 1.0 |

对比 off vs llm：faithfulness 胜 0 / 平 5 / 负 3，下降集中在 s08(-0.50)/s01(-0.33)/s07(-0.14)。

结论：
- 检索指标（CP/CR）完全不变 → 改写未改变召回集合（基线已饱和，与 §4 一致）；
- 生成侧 faithfulness 反而下降 → 双路检索使生成器采信了排序微变的块（或 judge 方差）；
- **端到端再次确认：改写默认关闭是正确决策**；若启用应走「触发式」（仅低召回时）。

系统整体基线（ground_truth 40 条，rewrite off）：
**CP 0.925 / CR 0.963 / faithfulness 0.923 / relevancy 1.0**。

复现：
```bash
python scripts/eval_quality.py --dataset data/eval/ground_truth_spoken.json --out data/eval/qe_spoken_off.json
python scripts/eval_quality.py --dataset data/eval/ground_truth_spoken.json --rewrite rule --out data/eval/qe_spoken_rule.json
python scripts/eval_quality.py --dataset data/eval/ground_truth_spoken.json --rewrite llm --out data/eval/qe_spoken_llm.json
python scripts/eval_quality.py --compare data/eval/qe_spoken_off.json data/eval/qe_spoken_llm.json
```

---

## 9. 图片语义描述（VLM）与图文双通道（补充 A/B）

> 这两个是**可选、默认关**的图片能力，用含图 GT 做了 A/B（详见 [RAG_OPTIMIZATION](RAG_OPTIMIZATION.md)）。

### 9.1 图片语义描述（`image_vlm_enabled`）
对 PDF 内嵌/扫描图用**视觉大模型**生成一句文本描述，混入文本向量通道，让「趋势 / 构图 / 示意图逻辑」等视觉语义可检索（不改变向量 schema）。

| 档 | MRR | Hit@1 | 结论 |
|----|-----|-------|------|
| off | 0.000 | 0.000 | 图内容完全不可检索 |
| on | **1.000** | **1.000** | 图内容全命中 |

- 默认 VLM = `deepseek-v4-flash-vision-exp`（官方、中文强、便宜，复用 `DEEPSEEK_API_KEY`）；也可配 `dashscope`/`openai`/`ollama`。
- 任意失败降级返回空串，不中断摄入。

### 9.2 图文双通道（`image_dual_channel`）
图片用**多模态向量**（`Chinese-CLIP` 默认）存独立 collection（`agent_images`），检索时用多模态文本编码器编码 query，与文本通道融合召回（像素级/"看一眼像"相似）。

| 档 | MRR | Hit@1 | Hit@5 | 结论 |
|----|-----|-------|-------|------|
| off | 0.000 | 0.000 | 0.000 | 纯图文档不可检索 |
| on | **0.750** | **0.667** | **1.000** | 图片向量召回 + 图像保底 |

**图片语义描述+图文双通道 组合（VLM 描述 + 图向量，含图 GT 4 条纯图问答）**：

| 配置 | MRR | Hit@1 | Hit@3 | Hit@5 | 说明 |
|------|-----|-------|-------|-------|------|
| 图文双通道（无图片语义描述） | 0.750 | 0.750 | 0.750 | 0.750 | "走势"类(img03)靠图向量弱 |
| 图片语义描述+图文双通道 | 0.583 | 0.250 | **1.000** | **1.000** | 召回满，但首名被 VLM 块占用 |
| **图片语义描述+图文双通道 + guard 排序调和** | **1.000** | **1.000** | **1.000** | **1.000** | 图片强制前置+移同位置 VLM 块 → 全 rank1 |

> **结论**：单纯叠加 图片语义描述+图文双通道 会"召回满但首名被 VLM 文本块抢走"；对图像保底做**排序调和**（相关图强制前置）后，**图片语义描述+图文双通道 达到满分、优于仅图文双通道**。推荐图片能力用"图片语义描述+图文双通道 同开 + guard 调和"。

- 需下载多模态模型：`huggingface-cli download OFA-Sys/chinese-clip-vit-base-patch16`（约 600MB，见 [SETUP](SETUP.md)）。
- 图片向量 collection 与 `delete_by_source` / `force_reingest` 同步，防 ghost。

复现：
```bash
# 图问答 off: 在含图 PDF 上摄入后 eval；on: 开 IMAGE_VLM_ENABLED=true 再摄入 eval
# 同型：开 IMAGE_DUAL_CHANNEL=true
python scripts/eval_rag.py --dataset data/eval/img_ground_truth.json   # （示例）
```