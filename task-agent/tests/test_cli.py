"""CLI 冒烟测试。"""
from __future__ import annotations

import argparse
import asyncio

from task_agent.cli import main, run_task


def _args(**kw) -> argparse.Namespace:
    defaults = dict(
        mode="replan",
        hitl=False,
        max_steps=8,
        findings_budget=None,
        llm="fake",
        tools=False,
        memory=False,
        event=False,
        json=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_run_task_fake_llm():
    result = asyncio.run(run_task("介绍一下公司", _args()))
    assert result.get("final_answer")


def test_run_task_with_tools():
    result = asyncio.run(run_task("计算一下", _args(tools=True)))
    assert result.get("final_answer")


def test_cli_main_run_json(capsys):
    assert main(["run", "目标", "--llm", "fake", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"final_answer"' in out
