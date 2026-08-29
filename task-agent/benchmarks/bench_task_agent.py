"""任务引擎基准：fixed vs replan 模式对比（步数/重试/耗时/完成率）。

离线模式（默认）用脚本化 FakeLLM，确定性、无需 key，主要比较**结构指标**
（执行步数、重试、耗时）；真实质量指标请用 `--llm openai` 配 API key。

用法（在 task-agent/ 下）：
    python benchmarks/bench_task_agent.py                     # 两种模式 × 3 任务 × 3 轮
    python benchmarks/bench_task_agent.py --mode replan --runs 5
    python benchmarks/bench_task_agent.py --llm openai --out results/bench.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from task_agent.config import TaskAgentConfig
from task_agent.demo import _OpenAICompatLLM
from task_agent.graph import build_agent
from task_agent.judge import judge_task

# 示例任务（自包含，不依赖外部知识）：真实 LLM 与离线 FakeLLM 均可作答
TASKS: list[dict] = [
    {
        "goal": "计算 1 到 100 所有质数的和",
        "expect": ["1060"],
        "answer": "1 到 100 的质数和为 1060。",
    },
    {
        "goal": "求 12 和 15 的最小公倍数",
        "expect": ["60"],
        "answer": "12 和 15 的最小公倍数是 60。",
    },
    {
        "goal": "用一句话介绍 RAG",
        "expect": ["RAG"],
        "answer": "RAG 是检索增强生成（Retrieval-Augmented Generation）。",
    },
]


class _ScriptedLLM:
    """按提示词特征返回脚本化输出的假 LLM（确定性、离线）。"""

    def __init__(self, tasks: list[dict]) -> None:
        self._answers = {t["goal"]: t["answer"] for t in tasks if t.get("answer")}
        self._replan_calls = 0
        self._check_calls = 0

    def _answer_for(self, goal: str) -> str:
        for key, ans in self._answers.items():
            if key in goal:
                return ans
        return "（脚本化答案）"

    async def ainvoke(self, prompt: str) -> Any:
        if "任务规划器" in prompt:  # PLAN_PROMPT
            return SimpleNamespace(
                content='[{"id": "1", "desc": "查询关键事实"}, {"id": "2", "desc": "汇总结论"}]'
            )
        if "可用信息来源" in prompt:  # REPLAN_PROMPT
            self._replan_calls += 1
            if self._replan_calls <= 2:
                return SimpleNamespace(
                    content='{"next_action": "查询关键事实", "expected_source": "kb"}'
                )
            return SimpleNamespace(content='{"next_action": "", "expected_source": "default"}')
        if "完成度检查员" in prompt:  # CHECK_PROMPT
            self._check_calls += 1
            return SimpleNamespace(
                content='{"done": false}' if self._check_calls == 1 else '{"done": true}'
            )
        if "结果整合器" in prompt:  # FINAL_PROMPT（含原始目标）
            goal = prompt.split("原始目标：", 1)[1].strip().split("\n", 1)[0].strip()
            return SimpleNamespace(content=self._answer_for(goal))
        # 执行步（DefaultExecutor 直答，prompt 即动作文本）
        return SimpleNamespace(content="执行完成，关键结论已获得。")


def _rule_judge(task: dict, answer: str) -> dict:
    """离线规则代理：按期望关键词覆盖率打分（真实质量用 --llm openai --judge）。"""
    expect = task.get("expect") or []
    hits = sum(1 for k in expect if k in answer)
    attainment = hits / len(expect) if expect else 0.0
    return {
        "goal_attainment": attainment,
        "info_completeness": attainment,
        "hallucination": 0.0,
        "comment": "规则判定（离线代理）",
    }


def _run_metrics(mode: str, runs: int, llm_kind: str, use_judge: bool = False) -> dict:
    """跑单模式：每个任务 × runs 轮，用事件回调统计步数/重试。"""
    openai_llm = _OpenAICompatLLM() if llm_kind == "openai" else None
    rows: list[dict] = []
    for task in TASKS:
        for _ in range(runs):
            if llm_kind == "openai":
                llm_factory = lambda: openai_llm
            else:
                scripted = _ScriptedLLM(TASKS)
                llm_factory = lambda: scripted  # 每轮新建，保证状态独立
            events: dict[str, int] = {}

            def _on_event(kind: str, data: dict) -> None:
                events[kind] = events.get(kind, 0) + 1

            agent = build_agent(
                config=TaskAgentConfig(mode=mode, hitl=False, findings_budget=10),
                llm_factory=llm_factory,
                checkpointer_provider=lambda: None,
                on_event=_on_event,
            )
            t0 = time.perf_counter()
            result = asyncio.run(agent.ainvoke({"goal": task["goal"]}))
            elapsed = time.perf_counter() - t0
            answer = str(result.get("final_answer") or "")
            row = {
                "task": task["goal"],
                "steps": events.get("execute", 0),
                "retries": events.get("verify", 0),
                "hitl": events.get("hitl", 0),
                "elapsed_s": round(elapsed, 3),
                "completed": bool(answer),
                "answer_ok": any(k in answer for k in task["expect"]),
            }
            if use_judge:
                if llm_kind == "openai":
                    judge = asyncio.run(
                        judge_task(
                            openai_llm,
                            task["goal"],
                            list(result.get("findings") or []),
                            answer,
                        )
                    )
                else:
                    judge = _rule_judge(task, answer)
                row["goal_attainment"] = judge["goal_attainment"]
                row["info_completeness"] = judge["info_completeness"]
                row["hallucination"] = judge["hallucination"]
                row["comment"] = judge.get("comment", "")
            rows.append(row)
    summary: dict = {
        "llm": llm_kind,
        "mode": mode,
        "runs": runs,
        "tasks": len(TASKS),
        "completion_rate": sum(r["completed"] for r in rows) / len(rows),
        "answer_ok_rate": sum(r["answer_ok"] for r in rows) / len(rows),
        "avg_steps": sum(r["steps"] for r in rows) / len(rows),
        "avg_retries": sum(r["retries"] for r in rows) / len(rows),
        "avg_elapsed_s": sum(r["elapsed_s"] for r in rows) / len(rows),
        "rows": rows,
    }
    if use_judge:
        summary.update(
            {
                "avg_goal_attainment": sum(r["goal_attainment"] for r in rows) / len(rows),
                "avg_info_completeness": sum(r["info_completeness"] for r in rows) / len(rows),
                "avg_hallucination": sum(r["hallucination"] for r in rows) / len(rows),
            }
        )
    return summary


def _print_table(results: list[dict], use_judge: bool) -> None:
    if use_judge:
        print(
            f"\n{'模式':<10}{'完成率':<8}{'目标达成':<10}{'信息完整':<10}{'幻觉':<8}{'平均步数':<10}{'平均耗时(s)':<10}"
        )
        print("-" * 66)
        for r in results:
            print(
                f"{r['mode']:<10}{r['completion_rate']:<8.3f}{r['avg_goal_attainment']:<10.3f}"
                f"{r['avg_info_completeness']:<10.3f}{r['avg_hallucination']:<8.3f}"
                f"{r['avg_steps']:<10.2f}{r['avg_elapsed_s']:<10.3f}"
            )
        return
    print(f"\n{'模式':<10}{'完成率':<8}{'答案命中':<8}{'平均步数':<10}{'平均重试':<8}{'平均耗时(s)':<10}")
    print("-" * 54)
    for r in results:
        print(
            f"{r['mode']:<10}{r['completion_rate']:<8.3f}{r['answer_ok_rate']:<8.3f}"
            f"{r['avg_steps']:<10.2f}{r['avg_retries']:<8.2f}{r['avg_elapsed_s']:<10.3f}"
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="task-agent 基准：fixed vs replan 对比")
    ap.add_argument("--mode", choices=["replan", "fixed", "all"], default="all")
    ap.add_argument("--runs", type=int, default=3, help="每个任务跑几轮")
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    ap.add_argument("--judge", action="store_true", help="LLM-judge 质量评估（离线=规则代理，openai=真实打分）")
    ap.add_argument("--out", default=None, help="保存 JSON 结果路径")
    args = ap.parse_args(argv)

    modes = ["replan", "fixed"] if args.mode == "all" else [args.mode]
    results = [_run_metrics(m, args.runs, args.llm, use_judge=args.judge) for m in modes]
    _print_table(results, use_judge=args.judge)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存: {args.out}")


if __name__ == "__main__":
    main()
