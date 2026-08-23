# 自主任务 Agent（task_agent/）

> 区别于主项目（Agentchat 的知识库问答/单轮路由）。这是一个**自主任务执行器**：
> 接受模糊目标 → LLM 分解子任务 → 循环执行（复用现有子 Agent）→ 结构化交付。

## 1. 与主项目的区别

| | 主项目（supervisor） | 本模块（task_agent） |
|--|--------------------|--------------------|
| 驱动 | 用户单轮提问 | 模糊复杂目标 |
| 执行模式 | ReAct 单循环 | **Plan → Execute → Final** |
| 视野 | 单步 | 多步规划（子任务列表） |
| 交付 | 直接回答 | 综合多个子结果的结构化交付 |

## 2. 架构（LangGraph StateGraph）

```
START → [plan] → [execute] ⇄(未完成)→ [final] → END
```

- **plan_node**：LLM 把目标拆成 2~8 个子任务（JSON）；解析失败回退“单子任务=直接回答”；
- **execute_node**：对当前子任务调用一次现有 supervisor（复用 rag/mcp/web_search/code 能力）执行，
  结果追加到 findings；单子任务异常不中断整个任务（标记 failed 继续）；
- **final_node**：整合所有 findings，输出最终交付。

### 二期 replan / 三期节点机制（默认）
```
START → [replan] → [confirm] → [execute] → {verify?} → [check] ⇄(未完成)→ [replan]
                                ↑ 失败重试 (HITL 可选)      ↓ 完成 → [final] → END
```
- **replan_node**：基于 goal + findings 每步动态决定下一步动作，并标注 `expected_source`(kb/db/web/code)；
- **confirm_node（节点级 HITL）**：replan 产出下一步后 `interrupt` 让用户确认(proceed/edit/skip)；依赖 Postgres checkpointer，未连库时自动降级全自主；`TASK_AGENT_HITL=false` 也可关；
- **execute_action_node**：按 `expected_source` 收紧开关 + 前缀引导(设计 A)执行；失败标记 finding；
- **verify_node（节点容错）**：子任务失败(失败/无输出)后自检(LLM 判是否值得重试)，未达 `TASK_AGENT_MAX_RETRIES` 则回 execute 重试(不计步数)，否则放弃进 check；
- **check_node**：判定是否充分达成 + `MAX_STEPS` 规则兜底防循环；
- **final_node**：整合所有 findings 输出交付。

## 3. 复用现有能力（零改造）

- LLM 工厂 `get_llm`、工具/子 Agent（rag/mcp/web_search/code）作执行器；
- Checkpointer（长任务可中断/恢复）、可观测（Langfuse）；
- 评估基建（FakeLLM 单测 + judge）。

## 4. 已验证

- 真实 LLM 跑通：目标「总结公司信息：成立时间/总部/旗舰产品 + 库是否有月球车资料」
  → 产出结构化对比表（两家公司分别列出，含否定型说明）；
- 单元测试 `tests/unit/test_task_agent.py`（35 条，含 verify/HITL/路由）：plan 解析（含代码围栏/非法回退）、plan_node、图路由。

## 5. 分期

- ✅ **一期（已完成）**：`fixed` 模式 Plan → Execute → Final + API；
- ✅ **二期（已完成）**：`replan` 模式——每步**动态重规划**(`replan_node`) + **独立 `check_node`** 判完成 + `MAX_STEPS` 防循环；
  `TASK_AGENT_MODE=fixed|replan` 可切（默认 replan）；
- ✅ **信息源感知（L1+L2）**：replan 输出 `expected_source`(kb/db/web/code)，execute 按 source 收紧开关 + 前缀引导（设计 A）——公司/产品信息优先知识库，不再联网查真实企业；
- ✅ **信息源感知（L1+L2）**：replan 输出 `expected_source`(kb/db/web/code)，execute 按 source 收紧开关 + 前缀引导（设计 A）——公司/产品信息优先知识库，不再联网查真实企业；
- ✅ **三期（已完成）**：节点级机制——**节点级 HITL**(confirm_node: proceed/edit/skip, 依赖 checkpointer, 无库降级全自主) + **节点容错**(verify_node: 失败自检重试, 不计步数) + **节点执行优化**(统一 LLM 重试 `_llm_text`、按 source 收紧工具; 失败/无输出/超时规则预筛; findings 状态用 reducer 增量更新);
- ✅ **Time Travel 长任务恢复**：`list_task_history` 列出线程 checkpoint 历史(含 checkpoint_id/checkpoint_ns/next/是否中断)；
  `agent-tasks/run` 支持 `checkpoint_id`——带去 goal 则从历史点**分叉**(update_state 改 state 后续跑)、不带去则**重放**(从该点重跑)；依赖 Postgres checkpointer，未连库安全返回空。
- ⬜ 待办：前端展示过程 + 流式(SSE) 接入。