"""宿主侧任务级评估：真实 LLM 跑示例目标 → task_agent.judge 打质量分。

用法（需 LLM key；backend/ 目录下）：
    python scripts/eval_task_agent.py
    python scripts/eval_task_agent.py --max-cases 3 --out data/eval/task_agent_eval.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from task_agent.judge import judge_task  # noqa: E402

from app.agents.llm import get_llm  # noqa: E402
from app.agents.task_agent_adapter import build_host_task_agent  # noqa: E402
from app.evaluation import setup_utf8_stdio  # noqa: E402

setup_utf8_stdio()

# 示例目标：自包含计算 + 依赖宿主知识库/工具两类
CASES: list[dict] = [
    {"goal": "计算 1 到 100 所有质数的和", "expect": ["1060"]},
    {"goal": "求 12 和 15 的最小公倍数", "expect": ["60"]},
    {"goal": "介绍一下平台的核心能力", "expect": []},
]


async def _run_once(graph, case: dict) -> dict:
    thread = f"task-eval-{uuid.uuid4().hex[:8]}"
    try:
        result = await graph.ainvoke(
            {"goal": case["goal"]},
            config={"configurable": {"thread_id": thread}},
        )
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断
        return {"goal": case["goal"], "error": f"{type(exc).__name__}: {exc}"}
    answer = str(result.get("final_answer") or "")
    judge = await judge_task(
        get_llm("light"),
        case["goal"],
        list(result.get("findings") or []),
        answer,
    )
    return {
        "goal": case["goal"],
        "steps": int(result.get("step") or 0),
        "answer_ok": any(k in answer for k in case["expect"]),
        "goal_attainment": judge["goal_attainment"],
        "info_completeness": judge["info_completeness"],
        "hallucination": judge["hallucination"],
        "comment": judge["comment"],
        "final_answer": answer[:200],
    }


async def _run_all(max_cases: int) -> list[dict]:
    graph = build_host_task_agent()
    return await asyncio.gather(
        *(_run_once(graph, c) for c in CASES[:max_cases])
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="宿主侧任务级评估（task_agent + LLM-judge）")
    ap.add_argument("--max-cases", type=int, default=len(CASES))
    ap.add_argument("--out", default=None, help="保存 JSON 结果路径")
    args = ap.parse_args()

    print("== task-agent 宿主评估（真实 LLM + LLM-judge）==")
    records = asyncio.run(_run_all(args.max_cases))
    ok = [r for r in records if "error" not in r]
    print(
        f"\n{'目标':<24}{'步数':<6}{'答案命中':<8}{'达成':<8}{'完整':<8}{'幻觉':<8}"
    )
    print("-" * 62)
    for r in records:
        if "error" in r:
            print(f"{r['goal']:<24}  ERROR: {r['error']}")
            continue
        print(
            f"{r['goal']:<24}{r['steps']:<6}{str(r['answer_ok']):<8}"
            f"{r['goal_attainment']:<8.3f}{r['info_completeness']:<8.3f}{r['hallucination']:<8.3f}"
        )
    if ok:
        print(
            f"\n平均：达成 {sum(r['goal_attainment'] for r in ok)/len(ok):.3f} / "
            f"完整 {sum(r['info_completeness'] for r in ok)/len(ok):.3f} / "
            f"幻觉 {sum(r['hallucination'] for r in ok)/len(ok):.3f}"
        )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存: {args.out}")
    return 0 if not any("error" in r for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
