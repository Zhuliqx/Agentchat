# Changelog

## 0.1.0（2026-08-30）

首个可安装版本（已发布 TestPyPI：`agentchat-task-agent`）。

**引擎**
- fixed / replan 双模式；plan → execute → check → verify → final 节点流
- 节点级 HITL（interrupt / Command resume，proceed/edit/skip），无 checkpointer 自动降级
- verify 自检重试、`retry_policy` + `timeout` + `error_handler` 节点容错
- Time Travel（`list_task_history` + `checkpoint_id` 分叉/重放）；findings reducer

**深度工程化**
- 事件流回调 `on_event`（plan/replan/execute/check/verify/hitl/final）
- findings 压缩 `findings_budget`（历史压缩进 `findings_summary`）
- 任务级质量评估 `task_agent.judge`（LLM-judge：目标达成 / 信息完整 / 幻觉）
- 容错混沌测试（执行器随机失败 / 永久失败 / LLM 永久失败均收敛）
- 基准 `benchmarks/bench_task_agent.py`（fixed vs replan，含 LLM-judge）

**广度**
- `ToolCallingExecutor` + 内置工具（calculator / current_time / random_number，零依赖）
- CLI `task-agent`（run / demo，支持 --llm / --tools / --memory / --event / --json）
- 跨任务记忆 `TaskMemory` / `InMemoryMemory`（引擎侧召回 + 沉淀）
- 可观测 `telemetry`（Langfuse span，缺依赖自动降级）

**宿主集成（Agentchat）**
- 适配器注入记忆（Postgres Store）与事件透传；新增 SSE 端点 `/api/agent-tasks/run/stream`
- 宿主评估脚本 `backend/scripts/eval_task_agent.py`

## 版本策略

改动后：`pyproject.toml` bump 版本 → 更新本文件 → `python -m build` → 上传对应仓库
（TestPyPI 先测，正式 PyPI 用项目级令牌）。
