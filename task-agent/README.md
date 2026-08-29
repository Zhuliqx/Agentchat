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
| 工具调用执行器 | `ToolCallingExecutor` + 内置 calculator/time/random（零依赖） |
| 跨任务记忆 | 任务结论沉淀与召回（`InMemoryMemory` 或宿主实现 `TaskMemory`） |

## 安装

```bash
pip install -e task-agent              # 本地路径依赖（仓库内目录）
# 或
pip install agentchat-task-agent       # 从 PyPI 安装（发行名）
# 或
pip install 'agentchat-task-agent[all]'  # openai + observability（Langfuse）
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
    on_event=on_event,                            # 可选: (kind, data) 事件回调
)
result = await agent.ainvoke({"goal": "..."})     # -> {findings, final_answer, ...}
```

接口缝（详见 `src/task_agent/`）：
- `TaskAgentConfig`：mode / hitl / max_retries / max_steps / llm_timeout / llm_max_retries /
  findings_budget（findings 保留上限，超限自动压缩历史）；
- `LLM` / `LLMFactory`：仅需 `async ainvoke(prompt)`；
- `CheckpointerProvider`：返回 LangGraph checkpointer 或 None（无状态降级）；
- `Executor` / `ExecuteRequest(action, source)` / `StepResult(answer)`：每步执行端口；
- `memory`：可选跨任务记忆（`build_agent(..., memory=...)`）。

## 工具调用执行器（开箱即用）

不依赖宿主也能"自己干活"：`ToolCallingExecutor` 让 LLM 决定调工具或直答，内置纯计算工具
（calculator / current_time / random_number，全部零依赖、AST 白名单求值）：

```python
from task_agent import TaskAgentConfig, build_agent
from task_agent.tools import ToolCallingExecutor, builtin_tools

agent = build_agent(
    config=TaskAgentConfig(mode="replan", hitl=False),
    llm_factory=llm_factory,
    executor=ToolCallingExecutor(llm_factory, builtin_tools),
)
```

宿主也可实现自己的 `Executor` 注入（接口缝不变）。工具声明用零依赖的 `Tool` dataclass
（name / description / parameters / func），支持同步与异步函数。

## CLI

```bash
task-agent run "介绍一下公司并计算质数和" --llm openai --tools --event --json
task-agent run "目标" --mode fixed --findings-budget 5
task-agent demo
```

`--llm fake`（默认）离线脚本化；`--tools` 启用内置工具；`--memory` 启用跨任务记忆；
`--event` 打印执行过程。

## 跨任务记忆

`build_agent(..., memory=...)` 传入实现 `TaskMemory` 的对象：
任务开始时按目标召回历史结论（注入 replan/plan 上下文），结束后把 `final_answer` 沉淀回记忆。
内置 `InMemoryMemory`（关键词召回，进程内）；宿主可对接自己的 Store 实现。

## 可观测

`on_event` 是唯一接入点，可接任意可观测后端：

```python
from task_agent.telemetry import langfuse_event_sink

agent = build_agent(..., on_event=langfuse_event_sink())  # 需 task-agent[observability]
```

未安装 Langfuse 时自动降级为控制台输出，不抛错。

## 发布到 PyPI

```bash
cd task-agent
python -m build
python -m twine upload --repository testpypi dist/*     # 先发 test-PyPI
```

发行名 `agentchat-task-agent`（import 名 `task_agent`，CLI 命令 `task-agent`）。

## 事件回调（过程可见）

`build_agent(on_event=...)` 会收到生命周期事件：`plan / replan / execute / check / verify / hitl / final`，
数据形如 `{"action": ..., "source": ..., "ok": ...}`。宿主可接 SSE 或日志：

```python
def on_event(kind: str, data: dict) -> None:
    print(f"[{kind}] {data}")
```

## 长任务记忆治理（findings 压缩）

长任务 `findings` 会持续累积。设 `TaskAgentConfig(findings_budget=N)` 后，超过 N 条时把历史
交给 LLM 压缩进 `findings_summary`，仅保留最新一条——控制后续 replan/check/final 的上下文
与 token 成本（LLM 失败自动退化为截断拼接，不中断执行）。

## 基准与容错

```bash
cd task-agent
python benchmarks/bench_task_agent.py                       # fixed vs replan 结构指标对比（离线）
python benchmarks/bench_task_agent.py --judge               # 追加质量评估（离线=规则代理）
python benchmarks/bench_task_agent.py --llm openai --judge --out results/bench.json  # 真实 LLM 指标 + LLM-judge 打分
```

离线模式统计完成率 / 答案命中 / 平均执行步数 / 平均重试 / 平均耗时；真实质量指标用
`--llm openai`（配 `TASK_AGENT_OPENAI_API_KEY`）；`--judge` 开启 LLM-judge 质量评估
（目标达成度 / 信息完整性 / 幻觉，0-1，见 `src/task_agent/judge.py`）。容错通过混沌测试验证
（`tests/test_resilience.py`：执行器随机失败 / 永久失败 / LLM 永久失败均能收敛交付）。

### 实测快照（真实 LLM：DeepSeek-chat，3 自包含任务 × 2 轮，2026-08-29）

| 模式 | 完成率 | 目标达成 | 信息完整 | 幻觉 | 平均步数 | 平均耗时 |
|------|--------|----------|----------|------|----------|----------|
| replan | 1.000 | 1.000 | 1.000 | 0.000 | **1.0** | **6.0s** |
| fixed | 1.000 | 1.000 | 1.000 | 0.000 | 3.0 | 12.0s |

**关键发现**：简单自包含任务上，replan 一步收敛（LLM 直答 → check 判完成），fixed 因
"先拆计划"多出 2 个执行步、耗时翻倍；而离线脚本化基准中 fixed 反而更快（脚本化计划恰好 2 步）。
结论：**脚本化基准只能测结构，真实 LLM 才能暴露行为差异**——两者都要跑，别只用离线数字下结论。

## 目录

```
task-agent/
├── pyproject.toml
├── benchmarks/bench_task_agent.py  # fixed vs replan 基准
├── src/task_agent/
│   ├── config.py      # TaskAgentConfig（运行配置）
│   ├── llm.py         # LLM 协议 + 文本抽取
│   ├── executor.py    # ExecuteRequest / StepResult / Executor / DefaultExecutor
│   ├── state.py       # TaskState（findings reducer）
│   ├── prompts.py     # PLAN/FINAL/REPLAN/CHECK/VERIFY/COMPRESS 提示词
│   ├── judge.py       # 任务级质量评估（LLM-judge，0-1 三指标）
│   ├── tools.py       # ToolCallingExecutor + 内置工具（零依赖）
│   ├── memory.py      # TaskMemory / InMemoryMemory（跨任务记忆）
│   ├── telemetry.py   # 控制台 / Langfuse 事件接入
│   ├── cli.py         # task-agent 命令行入口
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
默认执行器 / Time Travel / fixed 全流程 / demo 离线全流程 / 容错混沌注入 / findings 压缩 / 事件流。

## 与宿主应用的关系

本包不依赖任何宿主代码；主项目（Agentchat）通过 `backend/app/agents/task_agent_adapter.py`
注入 LLM / Checkpointer / `run_agent` 执行器，继续在 `/api/agent-tasks/*` 提供 API。
