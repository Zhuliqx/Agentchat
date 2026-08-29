# 自主任务 Agent（task-agent）

> 最后校验：2026-08-29（独立包实现；宿主集成见 [docs/AGENT_TASK.md](../docs/AGENT_TASK.md)）

> 独立的**长任务自主执行器**：接收模糊复杂目标 → LLM 分解 / 每步重规划 → 循环执行（注入 Executor）→ 结构化交付。
> 零业务依赖的独立 Python 包；宿主应用通过适配器注入 LLM / Checkpointer / 每步执行器。

## 一句话

面向"**模糊长目标**"的 Agentic 编排：把 LangGraph 的**交互式 HITL / 容错 / 时间旅行 / 状态管理**组合成一套可用的自主任务引擎。

## 核心能力

| 能力 | 说明 |
|------|------|
| 每步动态重规划 | `replan`（默认）；也可 `mode="fixed"` 一次计划 |
| 独立完成度判断 | `check` 判是否达成 + `max_steps` 防循环 |
| 信息源感知 | replan 标注 `expected_source`(kb/db/web/code)，宿主按源收紧开关 |
| 节点级 HITL | `interrupt` / resume，proceed/edit/skip；无 checkpointer 自动降级全自主 |
| verify 自检重试 | 子任务失败 → LLM 判是否重试（不计步数，`max_retries` 上限） |
| 节点级容错 | `retry_policy` + `timeout` + `error_handler`（返回 `Command`） |
| Time Travel | `list_task_history` + `checkpoint_id` 分叉 / 重放 |
| 状态治理 | `findings` reducer 增量合并 |

## 安装

```bash
pip install -e task-agent              # 本地路径依赖
# 或
pip install -e 'task-agent[openai]'    # 启用 OpenAI 兼容 LLM（demo 用）
```

## 快速体验（离线 demo，无需 API key）

```bash
python -m task_agent.demo
```

输出 replan 全流程的 findings 与 final_answer。设置 `TASK_AGENT_OPENAI_API_KEY`（可选 `TASK_AGENT_OPENAI_BASE_URL` / `TASK_AGENT_OPENAI_MODEL`）后自动切换真实 OpenAI 兼容端点。

## 编程接口

```python
from task_agent import TaskAgentConfig, build_agent
from task_agent.executor import ExecuteRequest, StepResult

async def my_executor(request: ExecuteRequest) -> StepResult:
    # 宿主在此提供真实工具能力（检索/数据库/搜索/代码…）
    return StepResult(answer=f"执行了 {request.action}")

agent = build_agent(
    config=TaskAgentConfig(mode="replan", hitl=True),
    llm_factory=llm_factory,                      # Callable[[], LLM]
    checkpointer_provider=checkpointer_provider,  # Callable[[], Any | None]
    executor=my_executor,                         # 缺省为纯 LLM 直答
)
result = await agent.ainvoke({"goal": "..."})     # -> {findings, final_answer, ...}
```

接口缝（详见 `src/task_agent/`）：
- `TaskAgentConfig`：mode / hitl / max_retries / max_steps / llm_timeout / llm_max_retries；
- `LLM` / `LLMFactory`：仅需 `async ainvoke(prompt)`；
- `CheckpointerProvider`：返回 LangGraph checkpointer 或 None（无状态降级）；
- `Executor` / `ExecuteRequest(action, source)` / `StepResult(answer)`：每步执行端口。

## 目录

```
task-agent/
├── pyproject.toml
├── src/task_agent/
│   ├── config.py      # TaskAgentConfig（运行配置）
│   ├── llm.py         # LLM 协议 + 文本抽取
│   ├── executor.py    # ExecuteRequest / StepResult / Executor / DefaultExecutor
│   ├── state.py       # TaskState（findings reducer）
│   ├── prompts.py     # PLAN/FINAL/REPLAN/CHECK/VERIFY 提示词
│   ├── nodes.py       # 节点（闭包注入 Runtime）
│   ├── graph.py       # build_agent / list_task_history / 路由 / error_handler
│   └── demo.py        # 离线 demo（脚本化 FakeLLM）
└── tests/
```

## 测试

```bash
cd task-agent && pytest -q
```

覆盖：解析 / 路由 / HITL（含无 checkpointer 降级）/ verify / error_handler / 执行节点 /
默认执行器 / Time Travel / fixed 全流程 / demo 离线全流程。

## 与宿主应用的关系

本包不依赖任何宿主代码；主项目（Agentchat）通过 `backend/app/agents/task_agent_adapter.py`
注入 LLM / Checkpointer / `run_agent` 执行器，继续在 `/api/agent-tasks/*` 提供 API。
