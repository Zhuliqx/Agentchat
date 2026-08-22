# Agent 编排质量评估（eval_agent.py）

> 目的：量化 supervisor 的**路由决策质量**——该用 RAG 的用对了吗？危险操作拒绝了吗？
> 这是评估体系的补盲：RAG 评估管「检索+生成质量」，Agent 评估管「编排是否正确」。

## 1. 设计

- **任务集** `data/eval/agent_tasks.json`（人工设计，gitignored）：14 条，覆盖五类；
  固定配置 `use_rag=true / use_search=false / use_memory=false / code 关`；
- **执行**：真实 LLM 驱动 `run_agent`，从 `tool` 事件提取实际工具序列；
- **判定（规则，零 LLM 成本）**：
  - `route@1`：首次工具选择正确（chat 型要求无调用）；
  - `tool_set_accuracy`：实际调用集合 == 期望集合；
  - `completion`：产生最终答案；
  - `refuse_accuracy`：危险操作**拒绝 或 触发 HITL 人工确认**（挂起等授权 = 安全行为）。

## 2. 结果（2026-08-22，runs=3 多采样）

| 指标 | 值 | 说明 |
|------|-----|------|
| **route@1** | **1.000** | 首次路由全对（14 条 × 3 次） |
| **tool_set_accuracy** | **0.714** | 4 类任务稳定多调 `code_agent` |
| **completion** | **0.976** | 41/42 次产生最终答案 |
| **refuse_accuracy** | **1.000** | 危险操作全部拒绝 / HITL |
| avg_tool_calls | 1.07 | 效率 |

逐条：rag×5、chat×2、multi(a14) 全过；mcp(a06-a08) 与 multi(a13) 存在**多余 `code_agent` 调用**。

## 3. 关键发现

1. **首次路由 100% 正确**——意图→工具的映射稳定（知识库/闲聊/危险操作判定都准）；
2. **真实编排缺陷**：统计/时间类任务 supervisor 稳定地**额外调用 `code_agent`**（即使 mcp_agent
   已返回可直接用的结果）——这是「效率」层面的质量问题，非正确性；
3. **评估驱动优化**：收紧 supervisor prompt 中 code_agent/mcp_agent 边界后，单次
   tool_set_accuracy 0.667→0.75；但模型倾向顽固，多采样 0.71——**LLM 路由存在固有偏差**，
   优化空间在 prompt/工具描述，且需多采样验证；
4. **方法论**：LLM 路由有随机性（同任务单次时好时坏）——**必须 `--runs N` 多采样取均值**，
   单次结果不可信（这是 Agent 评估与 RAG 评估最本质的方法论差异）。

## 4. 复现

```bash
python scripts/eval_agent.py --runs 3 --out data/eval/agent_eval.json
python scripts/eval_agent.py --max-cases 5 --runs 1   # 试跑
```

## 5. 扩展

- `search` 型（需 TAVILY key）、`code` 型（需 CODE_AGENT_ENABLED）任务已预留为扩展集；
- 可加 `answer_relevancy` judge 复评 answer 型任务的答案质量（当前只用规则判工具）。