# 查询改写(Query Rewriting)实验报告

> 目的：验证「查询改写」能否提升检索质量（口语 query × 书面文档的语义鸿沟）。
> 结论先行：**当前知识库基线已饱和，改写无增益，默认关闭**；但基础设施（两档改写、
> 双路兜底、防退化、A/B 评估）已就绪，且实验过程产出了 3 个重要的工程结论。

---

## 1. 方案与落地

| 模块 | 说明 |
|------|------|
| `app/rag/query_rewrite.py` | 两档改写：`rule`（去口语框架词/句尾疑问词 + 同义词**并列扩展**，零依赖）、`llm`（one-shot prompt 改写为精炼检索词，失败/拒绝模板自动回退） |
| `app/rag/retriever.py` | `_expand_queries` 双路检索（原 query + 改写结果），按 `(source, chunk_index)` 合并去重 |
| 防退化 | ① 精确词豁免：含数字/型号/英文专名跳过 llm 档；② 拒绝词回退：LLM 输出"请提供问题"等模板即回退原句；③ 双路兜底：改写丢信息时原句仍能召回 |
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
