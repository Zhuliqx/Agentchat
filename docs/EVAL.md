# 评测总入口

> 本页只做**索引与对照**：评测资产在哪、谁和谁配对、跑哪个脚本、数字以哪里为准。
> 最后校验：2026-09-17（防漂移检查见 `backend/scripts/check_docs_stale.py`）

## 1. 数字以哪里为准

- **唯一基线**：[docs/README.md §唯一基线](README.md)（检索 / 生成 / 消融 / Agent / 性能 / 测试规模）
- **公开可复现**：[REPRODUCIBLE_EVAL.md](REPRODUCIBLE_EVAL.md)（示例语料 + 14 问，含复现步骤与预期值）

本页与其他文档**不再重复写数字**，只写"看哪里"和"怎么跑"。

## 2. 评估集（GT）清单

### 公开（随仓库分发）

| GT | 条数 | 配套语料 | 用途 |
|---|---|---|---|
| `data/eval/ground_truth.json` | 14 | `data/kb/`（5 篇示例文档） | 公开可复现基线 + CI 质量评估 |
| `backend/tests/fixtures/eval/demo_2cases.json` | 2 | —（演示数据） | 单测的加载用例 |
| `backend/tests/fixtures/eval/private_docs_v2.json` | 8 | 私有图片/表格文档（未入库） | 仅校验"可加载 + 含图片用例" |

### 私有（本机 `backend/data/eval/gt/`，`.gitignore` 已忽略）

| GT | 条数 | 用途 |
|---|---|---|
| `ground_truth.json` | 40 | 主集（q01–q40，人工标注），头条基线的来源 |
| `ground_truth_hard.json` / `_hard_large.json` | 10 / 26 | 难例（对比 / 计算 / 条件 / 筛选 / 状态型） |
| `ground_truth_spoken.json` / `_spoken_large.json` | 8 / 58 | 口语化提问（验证查询改写的真实收益） |
| `ground_truth_expand.json` | 34 | 多公司 / 多行业歧义难例（避免来源级饱和） |
| `img_ground_truth.json` | 4 | 图片语义 / 图文双通道专项（答案只存在于图内） |

> 私有 GT 与私有语料**只在本地保留**：换机器时需一并拷贝 `backend/data/`，否则头条基线无法复跑。
> 这类文件不应入库（含真实业务语料），确需入库时先用 `*.local.json` 命名占位再评估。

## 3. 脚本索引

| 脚本 | 评什么 | 需要 LLM | 典型输入 | 产出 |
|---|---|---|---|---|
| `scripts/eval_rag.py` | 检索级 MRR / Hit@K | 否 | 任一 GT + 已摄入语料（`--dataset` / `--user`） | `backend/data/eval_runs/rag_eval_<时间戳>.json` |
| `scripts/eval_quality.py` | 四指标（LLM-judge，对齐 RAGAS） | 是 | GT + 已摄入语料（固定 `default` 用户；默认 `data/eval/gt/ground_truth.json`） | `--out` 指定（CI 用 `ci_quality.json`），`--report` 出 Markdown |
| `scripts/eval_agent.py` | 编排指标（路由 / 完成 / 危险操作拒绝） | 是 | `--tasks`（默认 `data/eval/agent_tasks.json`）、`--out` | `--out` 指定（如 `data/eval_runs/agent/agent_eval.json`） |
| `scripts/eval_task_agent.py` | 任务级质量（独立包自带 judge） | 是 | 示例目标 | `--out` 指定（如 `data/eval_runs/task_agent_eval.json`） |
| `scripts/eval_embedding.py` | Embedding 选型（来源级命中对比） | 否 | 同一 GT + 相同块文本（默认 `data/eval/gt/ground_truth.json`） | 控制台对比表 + `data/eval_runs/embedding_eval_<时间戳>.json` |
| `scripts/eval_rag_sweep.py` | 混合检索超参 sweep / 三档复测 | 否 | `--dataset` 收 GT 文件名（相对 `data/eval/gt/`；三档复测另读 `ground_truth_spoken.json` / `ground_truth_hard.json`） | 控制台的 JSON 汇总 |
| `scripts/benchmark.py` | 性能压测（检索链路 / 完整对话） | 对话档需要 | 运行中的服务 | 控制台 |

私有主集的常用两条：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/eval_rag.py --dataset data/eval/gt/ground_truth.json --user default
.\.venv\Scripts\python.exe scripts/eval_quality.py --dataset data/eval/gt/ground_truth.json --max-cases 5
```

## 4. 目录约定（都不入库）

**数据集 与 产出 分开放**：数据集在 `backend/data/eval/`，评估产出统一写 `backend/data/eval_runs/`。

| 路径 | 放什么 |
|---|---|
| `data/eval/gt/` | 私有评估集（见第 2 节） |
| `data/eval/agent_tasks.json` | Agent 编排评估的任务集（输入） |
| `data/eval/img_source/` | 图片专项语料（年报 PDF、架构图、图表） |
| `data/eval_runs/rag_eval_<时间戳>.json` | 检索级结果 |
| `data/eval_runs/embedding_eval_<时间戳>.json` | Embedding 选型对比结果 |
| `data/eval_runs/agent/` · `task_agent_eval.json` | Agent 编排 / 任务级结果 |
| `data/eval_runs/ci_quality*` | CI 产物（由 Actions 上传为 artifact） |
| `data/eval_runs/orphan_audit_*` · `orphan_backups/` | 数据体检输出（`scripts/audit_orphan_data.py`） |
| `data/eval_runs/results/` | 历史产物（当前无脚本写入，保留以便回溯） |

## 5. CI 里怎么跑

CI 只用**公开**资产（细节见 [REPRODUCIBLE_EVAL.md](REPRODUCIBLE_EVAL.md)）：

| job / 步骤 | 语料 | 评估集 |
|---|---|---|
| `rag-quality` | `data/kb` → 用户 `default` | `data/eval/ground_truth.json`（`--max-cases 10` 限成本） |
| `rag-regression`（阈值回归） | `tests/fixtures/kb_ci.txt` → `default` | 无（用例自带关键词断言） |
| `rag-regression`（基线步，非阻塞） | `data/kb` → 用户 `evalexample`（与上面隔离） | `data/eval/ground_truth.json` |

> 语料与 GT 必须成对：`expected_sources` 按文件名子串匹配，喂错语料会让 `context_recall` 恒为 0。
> `tests/fixtures/eval/private_docs_v2.json` 指向未入库的私有文档，因此只作加载校验、不跑检索。
