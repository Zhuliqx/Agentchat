"""自主任务 Agent 的单元测试（不依赖真实 LLM/DB）：plan 解析 + 图路由。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.task_agent import nodes as nodes_mod
from app.task_agent.graph import _route_after_replan, _route_fixed, _route_replan


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


def test_route_after_replan_action_execute():
    assert _route_after_replan({"current_action": "查成立"}) == "execute"


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