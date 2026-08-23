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

## 3. 复用现有能力（零改造）

- LLM 工厂 `get_llm`、工具/子 Agent（rag/mcp/web_search/code）作执行器；
- Checkpointer（长任务可中断/恢复）、可观测（Langfuse）；
- 评估基建（FakeLLM 单测 + judge）。

## 4. 已验证

- 真实 LLM 跑通：目标「总结公司信息：成立时间/总部/旗舰产品 + 库是否有月球车资料」
  → 产出结构化对比表（两家公司分别列出，含否定型说明）；
- 单元测试 `tests/unit/test_task_agent.py`（7 条）：plan 解析（含代码围栏/非法回退）、plan_node、图路由。

## 5. 分期

- ✅ **一期（已完成）**：`fixed` 模式 Plan → Execute → Final + API；
- ✅ **二期（已完成）**：`replan` 模式——每步**动态重规划**(`replan_node`) + **独立 `check_node`** 判完成 + `MAX_STEPS` 防循环；
  `TASK_AGENT_MODE=fixed|replan` 可切（默认 replan）；
- ✅ **信息源感知（L1+L2）**：replan 输出 `expected_source`(kb/db/web/code)，execute 按 source 收紧开关 + 前缀引导（设计 A）——公司/产品信息优先知识库，不再联网查真实企业；
- ⬜ 三期：前端展示过程 + 流式 + verify 精化/HITL 计划确认。