"""task-agent 命令行入口。

用法：
    task-agent run "目标" [--mode replan|fixed] [--llm fake|openai] [--tools] [--memory] [--event] [--json]
    task-agent demo
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Callable

from task_agent.config import TaskAgentConfig
from task_agent.demo import _OpenAICompatLLM, _ScriptedLLM
from task_agent.graph import build_agent
from task_agent.memory import InMemoryMemory
from task_agent.tools import ToolCallingExecutor, builtin_tools


def _llm_factory(kind: str):
    if kind == "openai":
        llm = _OpenAICompatLLM()
        return lambda: llm
    scripted = _ScriptedLLM()
    return lambda: scripted


async def run_task(goal: str, args: argparse.Namespace) -> dict:
    """构建图并执行（供 CLI 与测试复用）。"""
    config = TaskAgentConfig(
        mode=args.mode,
        hitl=args.hitl,
        max_steps=args.max_steps,
        findings_budget=args.findings_budget,
    )
    executor = (
        ToolCallingExecutor(_llm_factory(args.llm), builtin_tools)
        if args.tools
        else None
    )
    on_event: Callable[[str, dict], None] | None = None
    if args.event:

        def _on_event(kind: str, data: dict) -> None:
            print(f"  [event] {kind}: {data}")

        on_event = _on_event

    agent = build_agent(
        config=config,
        llm_factory=_llm_factory(args.llm),
        checkpointer_provider=lambda: None,
        executor=executor,
        on_event=on_event,
        memory=InMemoryMemory() if args.memory else None,
    )
    return await agent.ainvoke({"goal": goal})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="task-agent", description="自主任务 Agent 命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="执行一个目标任务")
    p_run.add_argument("goal")
    p_run.add_argument("--mode", choices=["replan", "fixed"], default="replan")
    p_run.add_argument("--hitl", action="store_true", help="开启节点级人工确认（需 checkpointer）")
    p_run.add_argument("--max-steps", type=int, default=8)
    p_run.add_argument("--findings-budget", type=int, default=None, help="findings 压缩上限")
    p_run.add_argument("--llm", choices=["fake", "openai"], default="fake", help="fake=脚本化离线 / openai=真实端点")
    p_run.add_argument("--tools", action="store_true", help="启用内置工具（calculator/time/random）")
    p_run.add_argument("--memory", action="store_true", help="启用跨任务记忆（进程内）")
    p_run.add_argument("--event", action="store_true", help="打印执行事件")
    p_run.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    sub.add_parser("demo", help="离线 demo")

    args = ap.parse_args(argv)
    if args.cmd == "demo":
        from task_agent.demo import main as demo_main

        demo_main()
        return 0

    result = asyncio.run(run_task(args.goal, args))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\nfindings:")
        for i, f in enumerate(result.get("findings") or [], 1):
            print(f"  [{i}] {f}")
        print("\nfinal_answer:", result.get("final_answer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
