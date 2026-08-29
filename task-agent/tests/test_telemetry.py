"""可观测适配单元测试（无 langfuse 依赖时安全降级）。"""
from __future__ import annotations

from task_agent.telemetry import console_event_sink, langfuse_event_sink


def test_console_sink_callable_and_safe():
    sink = console_event_sink()
    sink("execute", {"action": "x"})  # 不抛错即可


def test_langfuse_sink_degrades_when_missing(capsys):
    sink = langfuse_event_sink()
    assert callable(sink)
    sink("replan", {"action": "y"})  # 未安装 langfuse → 控制台降级，不抛错
