# 🎯 自主任务 Agent（task_agent/）

> 独立的**长任务自主执行器**：接收模糊复杂目标 → LLM 分解 / 每步重规划 → 循环执行（复用子 Agent）→ 结构化交付。
> 与主项目 [Agentchat](../../../README.md) 同仓库，但作为**独立项目**可单独展示 / 运行。

## 一句话
面向"**模糊长目标**"的 Agentic 编排：把 LangGraph 的**交互式 HITL / 容错 / 时间旅行 / 状态管理**组合成一套可用的自主任务引擎。

## 技术栈
- **FastAPI** + **LangGraph**（`StateGraph` · `interrupt`/`Command` · `retry_policy` · `timeout` · `error_handler` · Checkpointer · time-travel）
- **DeepSeek**（LLM）· 复用项目 1 的 `rag_agent` / `mcp_agent` / `web_search` / `code_agent` 子 Agent
- **Milvus**（向量）· **Postgres+pgvector**（checkpointer / 长任务持久化）
- 复用项目 1 的 LLM 工厂 / 工具 / Checkpointer / 评估 / Langfuse

## 核心能力
| 能力 | 说明 |
|------|------|
| 每步动态重规划 | `replan`；也可 `TASK_AGENT_MODE=fixed` 一次计划 |
| 独立完成度判断 | `check` 判是否达成 + `MAX_STEPS` 防循环 |
| 信息源感知（L1+L2） | replan 标注 `expected_source`(kb/db/web/code)，执行按源收紧开关 + 前缀引导——公司/产品优先知识库 |
| 节点级 HITL | `interrupt`/`Command(resume)`，proceed/edit/skip；无 Postgres 自动降级全自主 |
| verify 自检重试 | 子任务失败 → LLM 判是否重试（不计步数，`MAX_RETRIES` 上限） |
| 节点级 fault tolerance | `retry_policy` + `timeout` + `error_handler`(返回 `Command`) + 自定义 `retry_on` |
| Time Travel 长任务恢复 | `list_task_history` + `run` 支持 `checkpoint_id` 分叉 / 重放 |
| 状态治理 | `findings: Annotated[list, reducer]` 增量合并 |

## 架构
```
START → [replan] → [confirm?] → [execute] → {失败→verify} → [check] ⇄(未完成)→ [replan]
                                      ↑ 重试         ↓ 完成 → [final] → END
```

## API
- `POST /api/agent-tasks/run`：`{goal, session_id?, checkpoint_id?, checkpoint_ns?}`——新建任务 / 分叉(带 goal) / 重放(无 goal)
- `POST /api/agent-tasks/confirm`：`{session_id, verb, action?, source?}`——HITL 恢复
- `POST /api/agent-tasks/history`：`{session_id, limit}`——列 checkpoint 历史（Time Travel）

## 配置（backend/.env）
- `TASK_AGENT_MODE`=`replan`（`fixed`/`replan`）
- `TASK_AGENT_HITL`=`true`（依赖 Postgres checkpointer，无库自动降级全自主）
- `TASK_AGENT_MAX_RETRIES`=`2`

## 目录
```
backend/app/task_agent/
├── graph.py      # 图（fixed/replan）+ 条件边路由 + error_handler + list_task_history
├── nodes.py      # 节点（plan/execute/final/replan/execute_action/check/verify/human_confirm）
├── state.py      # TaskState（含 findings reducer）
└── prompts.py    # PLAN/FINAL/REPLAN/CHECK/VERIFY 提示词
tests/unit/test_task_agent.py
```

## 验证
- 单元测试 `tests/unit/test_task_agent.py`（解析/路由/HITL/verify/error_handler/TimeTravel 无库降级），全量单测通过 + Ruff 干净；
- 真实 LLM 跑通：目标「示例科技有限公司成立于哪一年」→ 信息源感知选 **kb** → 知识库检索出 2020/北京/company.md；
- 真实链路：**HITL**（interrupt→resume proceed）、**verify**（失败→重试成功）、**Time Travel 分叉**（历史点改 goal 续跑）均验证。

## 与主项目的关系
复用主项目全部子 Agent / LLM 工厂 / Checkpointer / 评估 / Langfuse；主项目侧重单轮知识问答，本模块侧重**模糊目标的多步自主执行**。
