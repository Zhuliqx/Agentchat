# 自主任务 Agent（task_agent/）
> 区别于主项目（Agentchat 的知识库问答/单轮路由）。这是一个**自主任务执行器**：
> 接收模糊目标 → LLM 分解/每步重规划 → 循环执行（复用现有子 Agent）→ 结构化交付。

## 1. 与主项目的区别

| | 主项目（supervisor） | 本模块（task_agent） |
|--|--------------------|--------------------|
| 驱动 | 用户单轮提问 | 模糊复杂目标 |
| 执行模式 | ReAct 单循环 | `fixed`(一次性计划) / **`replan`(每步重规划)** |
| 视野 | 单步 | 多步规划（子任务列表/动态决策） |
| 交付 | 直接回答 | 综合多个子结果的结构化交付 |

## 2. 架构（LangGraph StateGraph）

两种模式共用 `TaskState`：`findings` 用 `Annotated[list, _append_findings]` **reducer 增量合并**（节点只回新增片段）。

### fixed（一期：Plan → Execute → Final）
```
START → [plan] → [execute] ⇄(未完成)→ [final] → END
```
- **plan_node**：LLM 拆成 2~8 个子任务（JSON），解析失败回退“单子任务=直接回答”；
- **execute_node**：对当前子任务调用一次现有 supervisor（复用 rag/mcp/web_search/code），结果追加进 findings；
  单子任务异常不中断（标记 failed 继续）；
- **final_node**：整合所有 findings 输出交付。

### replan（二期：每步重规划）+ 三期节点机制（默认）
```
START → [replan] → [confirm?] → [execute] → {失败→verify} → [check] ⇄(未完成)→ [replan]
                                      ↑ 重试         ↓ 完成 → [final] → END
```
- **replan_node**：基于 goal + findings 每步动态决定下一步动作 + 标注 `expected_source`(kb/db/web/code)；新动作时重试计数归零；
- **confirm_node（节点级 HITL）**：replan 产出下一步后 `interrupt` 让用户确认（proceed/edit/skip）；依赖 Postgres checkpointer，未连库自动降级全自主；`TASK_AGENT_HITL=false` 也可关；
- **execute_action_node**：按 `expected_source` 收紧开关 + 前缀引导（设计 A）；失败返回 finding（不计重试次数），重试计数保留；
- **verify_node（自检重试）**：子任务失败(失败/无输出)后 LLM 判是否值得重试，未达 `TASK_AGENT_MAX_RETRIES` 则回 execute（不计步数），否则放弃进 check；
- **check_node**：判定是否充分达成 + `MAX_STEPS` 规则兜底防循环；
- **final_node**：整合所有 findings 输出交付。

### 节点级 fault tolerance（LLM 节点）
- **`retry_policy`**：`RetryPolicy(max_attempts=2, retry_on=_is_transient)`——网络/连接/超时/5xx/限流重试，确定性错误（ValueError 等）不重试（避免对必然失败浪费调用）；
- **`timeout`**：节点超时 = `llm_timeout * (llm_max_retries + 1)`，给客户端重试留足空间、仍有硬上限；
- **`error_handler`**：重试耗尽后降级，返回 **`Command(update, goto)`** 才能续跑；每节点最多一个。
- 例外：`execute`/`execute_action` 是“业务子任务”（失败标记 finding → 交 verify 语义重试），**不参与**上述 retry/timeout/error_handler。

## 3. 复用现有能力（零改造）
- LLM 工厂 `get_llm`、工具/子 Agent（rag/mcp/web_search/code）作执行器；
- **Checkpointer**（长任务可中断/恢复 + HITL + Time Travel）；
- 可观测（Langfuse）；评估基建（FakeLLM 单测 + judge）。

## 4. API 与关键配置
- `POST /api/agent-tasks/run`：`{goal, session_id?, checkpoint_id?, checkpoint_ns?}`——新建任务，或带 `checkpoint_id` 分叉(带 goal)/重放(无 goal)；
- `POST /api/agent-tasks/confirm`：`{session_id, verb, action?, source?}`——HITL 恢复（Command(resume)）；
- `POST /api/agent-tasks/history`：`{session_id, limit}`——Time Travel 列 checkpoint 历史；
- 配置：`TASK_AGENT_MODE=fixed|replan`(默认 replan)、`TASK_AGENT_HITL=true`(默认，无库降级)、`TASK_AGENT_MAX_RETRIES=2`。

## 5. 已验证
- 真实 LLM 跑通：目标「示例科技有限公司成立于哪一年」→ 信息源感知选 **kb** → 知识库检索出 2020/北京/company.md；
- **节点级 HITL** 真实链路：interrupt → `Command(resume=proceed)` → 继续执行完成；
- **verify 自检重试**：子任务失败 → verify 判重试 → 重试成功（findings 同时留失败+成功记录）；
- **Time Travel 分叉**：从历史 checkpoint 改用新 goal → `update_state` → 续跑新分支；
- **fault tolerance**：LLM 网络错误被 `_is_transient` 重试，error_handler 降级收敛兜底；
- 单元测试 `tests/unit/test_task_agent.py`（覆盖解析/路由/HITL/verify/error_handler/TimeTravel 无库降级），全量单测通过 + Ruff 干净。

## 6. 分期
- ✅ **一期**：`fixed` Plan → Execute → Final + API；`TASK_AGENT_MODE=fixed`；
- ✅ **二期**：`replan` 每步动态重规划 + 独立 `check` 判完成 + `MAX_STEPS` 防循环；
- ✅ **信息源感知（L1+L2）**：replan 标注 `expected_source`，执行按源收紧开关 + 前缀引导——公司/产品优先知识库；
- ✅ **三期**：节点级 HITL（confirm_node）+ 节点容错（verify_node）+ 状态 reducer；
- ✅ **节点级 fault tolerance**：`retry_policy` + `timeout` + `error_handler`(Command) + 自定义 `retry_on`；
- ✅ **Time Travel 长任务恢复**：`list_task_history` + `run` 支持 `checkpoint_id` 分叉/重放；
- ⬜ 待办：前端展示过程 + 流式(SSE) 接入。
