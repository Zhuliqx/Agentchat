# 评测总入口

> 本页只做**索引与对照**：评测资产在哪、谁和谁配对、跑哪个脚本、数字以哪里为准。
> 最后校验：2026-09-17（防漂移检查见 `backend/scripts/check_docs_stale.py`）

## 1. 数字以哪里为准

- **唯一基线**：[docs/README.md §唯一基线](README.md)（检索 / 生成 / 消融 / Agent / 性能 / 测试规模）
- **公开可复现**：[REPRODUCIBLE_EVAL.md](REPRODUCIBLE_EVAL.md)（示例语料 + 14 问，含复现步骤与预期值）

本页与其他文档**不再重复写数字**，只写"看哪里"和"怎么跑"。

## 2. 评估集（GT）清单

| GT | 条数 | 配套语料 | 用途 |
|---|---|---|---|
| `data/eval/ground_truth.json` | 14 | `data/kb/`（5 篇示例文档） | 公开可复现基线 + CI 质量评估 |
| `backend/tests/fixtures/eval/demo_2cases.json` | 2 | —（演示数据） | 单测的加载用例 |
| `backend/tests/fixtures/eval/demo_fields_v2.json` | 8 | —（字段演示，source 为占位） | 覆盖图片/表格/去重/口语字段，仅校验解析 |

## 3. 脚本索引

| 脚本 | 评什么 | 需要 LLM | 典型输入 | 产出 |
|---|---|---|---|---|
| `scripts/eval_rag.py` | 检索级 MRR / Hit@K | 否 | 任一 GT + 已摄入语料（`--dataset` / `--user`） | `backend/data/eval_runs/rag_eval_<时间戳>.json` |
| `scripts/eval_quality.py` | 四指标（LLM-judge，对齐 RAGAS） | 是 | GT + 已摄入语料（固定 `default` 用户；`--dataset` 可覆盖默认路径） | `--out` 指定（CI 用 `ci_quality.json`），`--report` 出 Markdown |
| `scripts/eval_agent.py` | 编排指标（路由 / 完成 / 危险操作拒绝） | 是 | `--tasks`（默认 `data/eval/agent_tasks.json`）、`--out` | `--out` 指定（如 `data/eval_runs/agent/agent_eval.json`） |
| `scripts/eval_task_agent.py` | 任务级质量（独立包自带 judge） | 是 | 示例目标 | `--out` 指定（如 `data/eval_runs/task_agent_eval.json`） |
| `scripts/eval_embedding.py` | Embedding 选型（来源级命中对比） | 否 | 同一 GT + 相同块文本（`--dataset` 指向） | 控制台对比表 + `data/eval_runs/embedding_eval_<时间戳>.json` |
| `scripts/eval_rag_sweep.py` | 混合检索超参 sweep / 三档复测 | 否 | `--dataset` 收 GT 文件名（相对 `data/eval/gt/`；三档复测另读口语集与难例集，文件名见脚本） | 控制台的 JSON 汇总 |
| `scripts/benchmark.py` | 性能压测（检索链路 / 完整对话） | 对话档需要 | 运行中的服务 | 控制台 |

常用两条（用仓库自带的公开 GT，clone 后可跑）：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/eval_rag.py --dataset ..\data\eval\ground_truth.json --user default
.\.venv\Scripts\python.exe scripts/eval_quality.py --dataset ..\data\eval\ground_truth.json --max-cases 5
```

## 4. 目录约定

**数据集与产出分开放**：数据集由 `--dataset` / `--tasks` 指向 `backend/data/eval/`（该目录已 gitignore，clone 后为空，
需要自备数据集），评估产出统一写 `backend/data/eval_runs/`。

| 路径 | 放什么 |
|---|---|
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
> `tests/fixtures/eval/demo_fields_v2.json` 的用例覆盖图片/表格等字段，CI 只校验其可加载，不跑检索。
