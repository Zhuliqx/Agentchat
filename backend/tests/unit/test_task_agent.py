"""自主任务 Agent 的单元测试（不依赖真实 LLM/DB）：plan 解析 + 图路由。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langgraph.types import Command

from app.task_agent import nodes as nodes_mod
from app.task_agent import graph as graph_mod
from app.task_agent.graph import (
    _err_check,
    _err_final,
    _err_plan,
    _err_replan,
    _err_verify,
    _route_after_confirm,
    _route_after_execute,
    _route_after_replan,
    _route_after_verify,
    _route_fixed,
    _route_replan,
)


class _LLM:
    """返回预设文本的假 LLM(invoke 走 SimpleNamespace)。"""

    def __init__(self, text: str):
        self.text = text

    def invoke(self, prompt):
        return SimpleNamespace(content=self.text)

    async def ainvoke(self, prompt):
        return SimpleNamespace(content=self.text)


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

async def _plan(state):
    return await nodes_mod.plan_node(state)


def test_plan_node_uses_llm(monkeypatch):
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: _LLM('[{"id":"1","desc":"找公司信息"}]'))
    state = asyncio.run(_plan({"goal": "总结公司信息"}))
    assert state["plan"][0]["desc"] == "找公司信息"
    assert state["current_idx"] == 0
    assert state["findings"] == []


def test_plan_node_fallback_on_empty(monkeypatch):
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: _LLM("无法拆解"))
    state = asyncio.run(_plan({"goal": "随便"}))
    # 解析失败 → 回退单一子任务(直接回答目标)
    assert len(state["plan"]) == 1
    assert "随便" in state["plan"][0]["desc"]


# ---------------- 图路由 ----------------

def test_route_continue():
    assert _route_fixed({"current_idx": 0, "plan": [{}]}) == "continue"


def test_route_final():
    assert _route_fixed({"current_idx": 1, "plan": [{}]}) == "final"
# ---------------- 二期 replan/check ----------------

def test_parse_next_action():
    action, source = nodes_mod._parse_next_action('{"next_action": "查公司成立年份"}')
    assert action == "查公司成立年份" and source == "default"


def test_parse_next_action_with_source():
    action, source = nodes_mod._parse_next_action('{"next_action": "查X", "expected_source": "kb"}')
    assert source == "kb"


def test_parse_next_action_unknown_source_default():
    _, source = nodes_mod._parse_next_action('{"next_action": "查X", "expected_source": "nonsense"}')
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


# ---------------- 节点级 HITL ----------------

def test_route_after_confirm_proceed():
    assert _route_after_confirm({"_confirm_verb": "proceed"}) == "execute"


def test_route_after_confirm_edit():
    assert _route_after_confirm({"_confirm_verb": "edit"}) == "execute"


def test_route_after_confirm_skip():
    assert _route_after_confirm({"_confirm_verb": "skip"}) == "check"


def test_route_after_confirm_default_proceed():
    # 未设置确认标记(如 HITL 关闭透传) → 默认执行
    assert _route_after_confirm({}) == "execute"


def test_human_confirm_disabled_passthrough(monkeypatch):
    monkeypatch.setattr(nodes_mod.settings, "task_agent_hitl", False)
    s = asyncio.run(nodes_mod.human_confirm_node({"current_action": "查X"}))
    assert s["_confirm_verb"] == "proceed"  # 关闭时不 interrupt,透传 proceed


def test_apply_confirm_default_proceed():
    assert nodes_mod._apply_confirm({}, "查X", "kb") == {"_confirm_verb": "proceed"}


def test_apply_confirm_edit_uses_user_action():
    r = nodes_mod._apply_confirm({"verb": "edit", "action": "查总部", "source": "db"}, "查X", "kb")
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


def test_route_after_execute_failed_verify(monkeypatch):
    monkeypatch.setattr(nodes_mod.settings, "task_agent_max_retries", 2)
    assert _route_after_execute({"findings": ["子任务失败：t"], "retries": 0}) == "verify"


def test_route_after_execute_failed_max_check(monkeypatch):
    # 已达重试上限 → 不再自检,进 check
    monkeypatch.setattr(nodes_mod.settings, "task_agent_max_retries", 1)
    assert _route_after_execute({"findings": ["子任务失败：t"], "retries": 1}) == "check"


def test_route_after_execute_success_check():
    assert _route_after_execute({"findings": ["正常结果说明"], "retries": 0}) == "check"


def test_route_after_verify_retry():
    assert _route_after_verify({"should_retry": True}) == "execute"


def test_route_after_verify_check():
    assert _route_after_verify({"should_retry": False}) == "check"


def test_verify_node_retry(monkeypatch):
    fake = _LLM('{"retry": true, "reason": "网络抖动"}')
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: fake)
    monkeypatch.setattr(nodes_mod.settings, "task_agent_max_retries", 3)
    s = asyncio.run(nodes_mod.verify_node(
        {"goal": "g", "current_action": "查X", "findings": ["子任务失败：timeout"], "retries": 0}))
    assert s["should_retry"] is True and s["retries"] == 1


def test_verify_node_no_retry(monkeypatch):
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: _LLM('{"retry": false, "reason": "不可行"}'))
    s = asyncio.run(nodes_mod.verify_node(
        {"goal": "g", "current_action": "查X", "findings": ["子任务失败：x"], "retries": 1}))
    assert s["should_retry"] is False and s["retries"] == 0


def test_verify_node_hits_max(monkeypatch):
    # 达重试上限 → 直接放弃(不调 LLM)
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: None)
    monkeypatch.setattr(nodes_mod.settings, "task_agent_max_retries", 1)
    s = asyncio.run(nodes_mod.verify_node(
        {"goal": "g", "current_action": "查X", "findings": ["子任务失败：x"], "retries": 1}))
    assert s["should_retry"] is False and s["retries"] == 1


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
    assert isinstance(c, Command) and c.goto == graph_mod.END
    assert c.update["final_answer"]


def test_err_verify_returns_command():
    c = _err_verify({"retries": 3}, None)
    assert isinstance(c, Command) and c.goto == "check"
    assert c.update["should_retry"] is False and c.update["retries"] == 3


# ---------------- Time Travel(长任务恢复)----------------

def test_list_task_history_no_db(monkeypatch):
    # 未连 checkpointer(Postgres) → 安全返回空(与 HITL 无库降级一致)
    monkeypatch.setattr(graph_mod, "get_checkpointer", lambda: None)
    assert asyncio.run(graph_mod.list_task_history("x")) == []


async def _check(state):
    return await nodes_mod.check_node(state)


def test_check_force_done_on_max(monkeypatch):
    # step>=MAX_STEPS 规则兜底：不调用 LLM，直接 done
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: None)
    s = asyncio.run(_check({"goal": "g", "findings": [], "step": nodes_mod.MAX_STEPS}))
    assert s["done"] is True


def test_check_calls_llm(monkeypatch):
    fake = _LLM('{"done": false}')
    monkeypatch.setattr(nodes_mod, "get_llm", lambda kind: fake)
    s = asyncio.run(_check({"goal": "g", "findings": ["f"], "step": 1}))
    assert s["done"] is False