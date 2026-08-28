# Agent 编排质量评估（eval_agent.py）

> 相关文档：见 [文档地图](README.md)；项目 2 见 [AGENT_TASK](AGENT_TASK.md)。
> 最后校验：2026-08-29（结果与 `data/eval/agent/agent_eval.json` 快照一致；复现见 §4）

> 目的：量化 supervisor 的**路由决策质量**——该用 RAG 的用对了吗？危险操作拒绝了吗？
> 这是评估体系的补盲：RAG 评估管「检索+生成质量」，Agent 评估管「编排是否正确」。

## 1. 设计

- **任务集** `data/eval/agent_tasks.json`（人工设计，gitignored）：**17 条**，覆盖 7 类（rag/mcp/chat/refuse/multi/search/code）；
  rag/mcp/chat/refuse/multi 固定 `use_search=false`，search 型 `use_search=true`（联网），code 型走 `code_agent`；
- **执行**：真实 LLM 驱动 `run_agent`，从 `tool` 事件提取实际工具序列；
- **判定（规则，零 LLM 成本）**：
  - `route@1`：首次工具选择正确（chat 型要求无调用）；
  - `tool_set_accuracy`：实际调用集合 == 期望集合；
  - `completion`：产生最终答案；
  - `refuse_accuracy`：危险操作**拒绝 或 触发 HITL 人工确认**（挂起等授权 = 安全行为）。

## 2. 结果（runs=3 多采样；`--judge` 评答案质量）

| 指标 | 值 | 说明 |
|------|-----|------|
| **route@1** | **1.000** | 首次路由全对（17 条 × 3 次，含 search/code） |
| **tool_set_accuracy** | **0.841** | 较前期提升（search/code 全对贡献）；mcp 统计类仍多调 code_agent |
| **completion** | **0.980** | 50/51 次产生最终答案 |
| **refuse_accuracy** | **1.000** | 危险操作全部拒绝 / HITL |
| avg_tool_calls | 1.00 | 效率 |
| **answer_relevancy**（`--judge`） | **0.733** | LLM judge 评 answer 型任务答案相关度 |

逐条：rag×5、chat×2、refuse×2、search×2、code×1、multi(a14) 全过；mcp(a06-a08) 与 multi(a13) 存在**多余 `code_agent` 调用**。

## 3. 关键发现

1. **首次路由 100% 正确**——意图→工具的映射稳定（知识库/闲聊/危险操作判定都准）；
2. **真实编排缺陷**：统计/时间类任务 supervisor 稳定地**额外调用 `code_agent`**（即使 mcp_agent
   已返回可直接用的结果）——这是「效率」层面的质量问题，非正确性；
3. **评估驱动优化**：收紧 supervisor prompt 中 code_agent/mcp_agent 边界后，tool_set_accuracy
   从 0.714 提升到 **0.841**（search/code 型全对是主要贡献）；但 mcp 统计/时间类仍多调 code_agent
   （a06/a08 set=1/3）——**LLM 路由的固有偏差仍在**，优化空间在 prompt/工具描述；
4. **方法论**：LLM 路由有随机性（同任务单次时好时坏）——**必须 `--runs N` 多采样取均值**，
   单次结果不可信（这是 Agent 评估与 RAG 评估最本质的方法论差异）。

## 4. 复现

```bash
python scripts/eval_agent.py --runs 3 --out data/eval/agent_eval.json
python scripts/eval_agent.py --max-cases 5 --runs 1   # 试跑
```

## 5. 扩展（已实现）

- **search 型**（`use_search=true`，需 TAVILY key）、**code 型**（`code_agent`）已接入，均 3/3 正确路由；
- **answer_relevancy judge**：`--judge` 用 LLM judge 复评 answer 型任务答案质量（有成本，默认关；runs=1 时 0.733）。
