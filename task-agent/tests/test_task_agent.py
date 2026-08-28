"""自主任务 Agent 单元测试（不依赖真实 LLM/DB/宿主应用）。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langgraph.types import Command

from task_agent import nodes as nodes_mod
from task_agent.config import TaskAgentConfig
from task_agent.executor import DefaultExecutor, ExecuteRequest, StepResult
from task_agent.graph import (
    _err_check,
    _err_final,
    _err_plan,
    _err_replan,
    _err_verify,
    _is_transient,
    _route_after_confirm,
    _route_after_execute,
    _route_after_replan,
    _route_after_verify,
    _route_fixed,
    _route_replan,
    build_agent,
    list_task_history,
)
from task_agent.nodes import Runtime, _apply_confirm, make_nodes


class _LLM:
    """返回预设文本的假 LLM。"""

    def __init__(self, text: str):
        self.text = text

    async def ainvoke(self, prompt):
        return SimpleNamespace(content=self.text)


class _SequenceLLM:
    """按调用顺序返回预设文本的假 LLM（最后一次重复）。"""

    def __init__(self, *texts: str):
        self._texts = list(texts)
        self._i = 0

    async def ainvoke(self, prompt):
        text = self._texts[min(self._i, len(self._texts) - 1)]
        self._i += 1
        return SimpleNamespace(content=text)


class _FakeExecutor:
    """记录调用并返回预设答案的假执行器；可配置抛异常。"""

    def __init__(self, answer: str = "子任务结果", error: Exception | None = None):
        self.calls: list[ExecuteRequest] = []
        self._answer = answer
        self._error = error

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return StepResult(answer=self._answer)


def _runtime(
    config: TaskAgentConfig | None = None,
    llm: _LLM | None = None,
    executor: _FakeExecutor | None = None,
) -> Runtime:
    return Runtime(
        config=config or TaskAgentConfig(),
        llm_factory=lambda: (llm if llm is not None else _LLM("")),
        executor=executor or _FakeExecutor(),
    )


def _nodes(
    config: TaskAgentConfig | None = None,
    llm: _LLM | None = None,
    executor: _FakeExecutor | None = None,
) -> dict:
    return make_nodes(_runtime(config, llm, executor))


# ---------------- plan 解析 ----------------


def test_parse_plan_basic():
    text = "[{\"id\":\"1\",\"desc\":\"找成立时间\"},{\"id\":\"2\",\"desc\":\"找旗舰产品\"}]"
    plan = nodes_mod._parse_plan(text)
    assert len(plan) == 2
    assert plan[0]["desc"] == "找成立时间"
    assert plan[0]["status"] == "pending"


def test_parse_plan_strips_code_fence():
    text = "```json\n[{\"id\":\"1\",\"desc\":\"任务A\"}]```"
    plan = nodes_mod._parse_plan(text)
    assert len(plan) == 1 and plan[0]["desc"] == "任务A"


def test_parse_plan_invalid_returns_empty():
    assert nodes_mod._parse_plan("这不是JSON") == []


# ---------------- plan_node ----------------


def test_plan_node_uses_llm():
    nodes = _nodes(llm=_LLM('[{"id":"1","desc":"找公司信息"}]'))
    state = asyncio.run(nodes["plan_node"]({"goal": "总结公司信息"}))
    assert state["plan"][0]["desc"] == "找公司信息"
    assert state["current_idx"] == 0
    assert state["findings"] == []


def test_plan_node_fallback_on_empty():
    nodes = _nodes(llm=_LLM("无法拆解"))
    state = asyncio.run(nodes["plan_node"]({"goal": "随便"}))
    # 解析失败 → 回退单一子任务(直接回答目标)
    assert len(state["plan"]) == 1
    assert "随便" in state["plan"][0]["desc"]


# ---------------- 图路由 ----------------


def test_route_continue():
    assert _route_fixed({"current_idx": 0, "plan": [{}]}) == "continue"


def test_route_final():
    assert _route_fixed({"current_idx": 1, "plan": [{}]}) == "final"


def test_parse_next_action():
    action, source = nodes_mod._parse_next_action('{"next_action": "查公司成立年份"}')
    assert action == "查公司成立年份" and source == "default"


def test_parse_next_action_with_source():
    action, source = nodes_mod._parse_next_action(
        '{"next_action": "查X", "expected_source": "kb"}'
    )
    assert source == "kb"


def test_parse_next_action_unknown_source_default():
    _, source = nodes_mod._parse_next_action(
        '{"next_action": "查X", "expected_source": "nonsense"}'
    )
    assert source == "default"


def test_parse_next_action_empty():
    action, source = nodes_mod._parse_next_action('{"next_action": ""}')
    assert action == "" and source == "default"


def test_route_replan_done():
    assert _route_replan({"done": True}) == "final"


def test_route_replan_continue():
    assert _route_replan({"done": False}) == "replan"


def test_route_after_replan_empty_final():
    assert _route_after_replan({"current_action": ""}) == "final"


def test_route_after_replan_action_confirm():
    # replan 产出动作后 → 先交 HITL 确认(关闭时 human_confirm 透传 proceed)
    assert _route_after_replan({"current_action": "查成立"}) == "confirm"


def test_route_after_confirm_proceed():
    assert _route_after_confirm({"_confirm_verb": "proceed"}) == "execute"


def test_route_after_confirm_edit():
    assert _route_after_confirm({"_confirm_verb": "edit"}) == "execute"


def test_route_after_confirm_skip():
    assert _route_after_confirm({"_confirm_verb": "skip"}) == "check"


def test_route_after_confirm_default_proceed():
    # 未设置确认标记(如 HITL 关闭透传) → 默认执行
    assert _route_after_confirm({}) == "execute"


# ---------------- 节点级 HITL ----------------


def test_human_confirm_disabled_passthrough():
    nodes = _nodes(config=TaskAgentConfig(hitl=False))
    s = asyncio.run(nodes["human_confirm_node"]({"current_action": "查X"}))
    assert s["_confirm_verb"] == "proceed"  # 关闭时不 interrupt,透传 proceed


def test_human_confirm_no_checkpointer_passthrough():
    # 开启 HITL 但无 checkpointer → 降级全自主（不 interrupt）
    nodes = make_nodes(
        Runtime(
            config=TaskAgentConfig(hitl=True),
            llm_factory=lambda: _LLM(""),
            executor=_FakeExecutor(),
            checkpointer_provider=lambda: None,
        )
    )
    s = asyncio.run(nodes["human_confirm_node"]({"current_action": "查X"}))
    assert s["_confirm_verb"] == "proceed"


def test_apply_confirm_default_proceed():
    assert nodes_mod._apply_confirm({}, "查X", "kb") == {"_confirm_verb": "proceed"}


def test_apply_confirm_edit_uses_user_action():
    r = nodes_mod._apply_confirm(
        {"verb": "edit", "action": "查总部", "source": "db"}, "查X", "kb"
    )
    assert r == {"_confirm_verb": "edit", "current_action": "查总部", "expected_source": "db"}


def test_apply_confirm_edit_keeps_default_on_missing():
    # edit 但没给 action/source → 沿用当前值
    r = nodes_mod._apply_confirm({"verb": "edit"}, "查X", "kb")
    assert r == {"_confirm_verb": "edit", "current_action": "查X", "expected_source": "kb"}


def test_apply_confirm_skip():
    assert nodes_mod._apply_confirm({"verb": "skip"}, "查X", "kb") == {"_confirm_verb": "skip"}


# ---------------- verify 容错(失败自检重试) ----------------


def test_is_failed_finding():
    assert nodes_mod._is_failed_finding("子任务失败：timeout") is True
    assert nodes_mod._is_failed_finding("（子任务无输出）") is True
    assert nodes_mod._is_failed_finding("") is True
    assert nodes_mod._is_failed_finding("正常结果说明") is False


def test_route_after_execute_failed_verify():
    assert _route_after_execute(TaskAgentConfig(max_retries=2))(
        {"findings": ["子任务失败：t"], "retries": 0}
    ) == "verify"


def test_route_after_execute_failed_max_check():
    # 已达重试上限 → 不再自检,进 check
    assert _route_after_execute(TaskAgentConfig(max_retries=1))(
        {"findings": ["子任务失败：t"], "retries": 1}
    ) == "check"


def test_route_after_execute_success_check():
    assert _route_after_execute()({"findings": ["正常结果说明"], "retries": 0}) == "check"


def test_route_after_verify_retry():
    assert _route_after_verify({"should_retry": True}) == "execute"


def test_route_after_verify_check():
    assert _route_after_verify({"should_retry": False}) == "check"


def test_verify_node_retry():
    nodes = _nodes(
        config=TaskAgentConfig(max_retries=3),
        llm=_LLM('{"retry": true, "reason": "网络抖动"}'),
    )
    s = asyncio.run(
        nodes["verify_node"](
            {"goal": "g", "current_action": "查X", "findings": ["子任务失败：timeout"], "retries": 0}
        )
    )
    assert s["should_retry"] is True and s["retries"] == 1


def test_verify_node_no_retry():
    nodes = _nodes(
        config=TaskAgentConfig(max_retries=2),
        llm=_LLM('{"retry": false, "reason": "不可行"}'),
    )
    s = asyncio.run(
        nodes["verify_node"](
            {"goal": "g", "current_action": "查X", "findings": ["子任务失败：x"], "retries": 1}
        )
    )
    assert s["should_retry"] is False and s["retries"] == 0


def test_verify_node_hits_max():
    # 达重试上限 → 直接放弃(不调 LLM)
    def boom():
        raise AssertionError("LLM 不应被调用")

    nodes = make_nodes(
        Runtime(
            config=TaskAgentConfig(max_retries=1),
            llm_factory=boom,
            executor=_FakeExecutor(),
        )
    )
    s = asyncio.run(
        nodes["verify_node"](
            {"goal": "g", "current_action": "查X", "findings": ["子任务失败：x"], "retries": 1}
        )
    )
    assert s["should_retry"] is False and s["retries"] == 1


# ---------------- 执行节点 ----------------


def test_execute_node_runs_executor():
    executor = _FakeExecutor(answer="结果X")
    nodes = _nodes(executor=executor)
    plan = [{"id": "1", "desc": "任务A", "status": "pending", "result": ""}]
    s = asyncio.run(nodes["execute_node"]({"current_idx": 0, "plan": plan, "goal": "g"}))
    assert s["findings"] == ["结果X"]
    assert s["plan"][0]["status"] == "done"
    assert s["current_idx"] == 1
    assert executor.calls[0].source == "default"


def test_execute_node_failure_marks_failed():
    executor = _FakeExecutor(error=RuntimeError("boom"))
    nodes = _nodes(executor=executor)
    plan = [{"id": "1", "desc": "任务A", "status": "pending", "result": ""}]
    s = asyncio.run(nodes["execute_node"]({"current_idx": 0, "plan": plan, "goal": "g"}))
    assert s["plan"][0]["status"] == "failed"
    assert s["findings"][0].startswith("子任务失败")


def test_execute_action_node_passes_source_and_advances_step():
    executor = _FakeExecutor(answer="查询结果")
    nodes = _nodes(executor=executor)
    s = asyncio.run(
        nodes["execute_action_node"](
            {"current_action": "查X", "expected_source": "kb", "step": 1, "retries": 0, "goal": "g"}
        )
    )
    assert executor.calls[0].source == "kb"
    assert s["step"] == 2
    assert s["retries"] == 0
    assert s["findings"] == ["查询结果"]


def test_execute_action_empty_advances_step():
    nodes = _nodes()
    s = asyncio.run(nodes["execute_action_node"]({"current_action": "", "step": 1, "goal": "g"}))
    assert s["step"] == 2


# ---------------- error_handler(Command 降级) ----------------


def test_err_plan_returns_command():
    c = _err_plan({"goal": "总结公司"}, None)
    assert isinstance(c, Command) and c.goto == "execute"
    assert c.update["plan"][0]["desc"] == "请直接回答：总结公司"
    assert c.update["current_idx"] == 0


def test_err_replan_returns_command():
    c = _err_replan({"goal": "g"}, None)
    assert isinstance(c, Command) and c.goto == "final"
    assert c.update["done"] is True and c.update["current_action"] == ""


def test_err_check_returns_command():
    c = _err_check({}, None)
    assert isinstance(c, Command) and c.goto == "final" and c.update["done"] is True


def test_err_final_returns_command():
    c = _err_final({}, None)
    assert isinstance(c, Command) and c.goto == "__end__"  # langgraph.graph.END
    assert c.update["final_answer"]


def test_err_verify_returns_command():
    c = _err_verify({"retries": 3}, None)
    assert isinstance(c, Command) and c.goto == "check"
    assert c.update["should_retry"] is False and c.update["retries"] == 3


def test_is_transient():
    # 网络/连接/超时 → 值得重试
    assert _is_transient(ConnectionError("network")) is True
    assert _is_transient(TimeoutError("timeout")) is True
    # 确定性错误 → 不重试
    assert _is_transient(ValueError("bad input")) is False
    assert _is_transient(KeyError("key")) is False


# ---------------- Time Travel(长任务恢复) ----------------


def test_list_task_history_no_graph():
    # 未传入图(无 checkpointer) → 安全返回空
    assert asyncio.run(list_task_history(None, "x")) == []


# ---------------- check 节点 ----------------


def test_check_force_done_on_max():
    nodes = make_nodes(
        Runtime(
            config=TaskAgentConfig(max_steps=8),
            llm_factory=lambda: (_ for _ in ()).throw(AssertionError("LLM 不应被调用")),
            executor=_FakeExecutor(),
        )
    )
    s = asyncio.run(
        nodes["check_node"]({"goal": "g", "findings": [], "step": 8})
    )
    assert s["done"] is True


def test_check_calls_llm():
    nodes = _nodes(llm=_LLM('{"done": false}'))
    s = asyncio.run(nodes["check_node"]({"goal": "g", "findings": ["f"], "step": 1}))
    assert s["done"] is False


# ---------------- 默认执行器 / 完整图 / demo ----------------


def test_default_executor_llm_direct_answer():
    ex = DefaultExecutor(lambda: _LLM(" 直接答案 "))
    r = asyncio.run(ex(ExecuteRequest(action="查X", source="default")))
    assert r.answer == "直接答案"


def test_build_agent_fixed_mode_full_flow():
    llm = _SequenceLLM('[{"id":"1","desc":"查公司成立年份"}]', "最终答案")
    agent = build_agent(
        TaskAgentConfig(mode="fixed"),
        llm_factory=lambda: llm,
        executor=_FakeExecutor(answer="公司成立于2020年"),
    )
    result = asyncio.run(agent.ainvoke({"goal": "查公司成立年份"}))
    assert result.get("final_answer") == "最终答案"
    assert result.get("findings") == ["公司成立于2020年"]


def test_demo_flow_offline():
    from task_agent import demo

    result = asyncio.run(demo.run_demo_flow())
    assert result.get("final_answer")
    assert "2020" in result["final_answer"]
    assert len(result.get("findings") or []) >= 2
