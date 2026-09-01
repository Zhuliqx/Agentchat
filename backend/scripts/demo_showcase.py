"""演示录屏脚本：驱动本地运行的服务，依次展示 4 个面试场景。

前提：后端已启动（python run.py），LLM key 已配置；建议开启 HITL（HITL_ENABLED=true）。

用法：
    python scripts/demo_showcase.py                 # 全部场景
    python scripts/demo_showcase.py --scenario 1    # 只跑单个场景
    python scripts/demo_showcase.py --base http://localhost:8000

场景：
    1. RAG 多轮问答 + 引用溯源（/api/chat）
    2. HITL 人工确认 → resume 恢复（/api/chat）
    3. task-agent 长任务（/api/agent-tasks/run，含 HITL proceed 续跑）
    4. Time Travel 历史分叉（/api/agent-tasks/history）
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from typing import cast

import httpx

try:
    # sys.stdout 的 typeshed 类型是 TextIO（无 reconfigure）
    cast(io.TextIOWrapper, sys.stdout).reconfigure(
        encoding="utf-8", errors="replace"
    )
except Exception:
    pass


def _sep(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"▶ {title}")
    print("=" * 70)


def _dump(label: str, data) -> None:
    print(f"\n[{label}]")
    print(json.dumps(data, ensure_ascii=False, indent=2))


class Showcase:
    def __init__(self, base: str) -> None:
        self.client = httpx.Client(timeout=180)
        self.base = base

    def _post(self, path: str, payload: dict) -> dict:
        r = self.client.post(self.base + path, json=payload)
        r.raise_for_status()
        return r.json()

    def scenario1_rag(self) -> None:
        _sep("场景 1：RAG 多轮问答 + 引用溯源")
        session_id = None
        for q in ["公司成立于哪一年？", "那它的旗舰产品是什么？"]:
            payload = {"message": q, "use_rag": True, "use_search": False}
            if session_id:
                payload["session_id"] = session_id
            r = self._post("/api/chat", payload)
            session_id = r.get("session_id") or session_id
            print(f"\n问：{q}")
            print(f"答：{r.get('answer', '')[:200]}")
            print(f"用到的 Agent：{r.get('used_agents')}")
            sources = []
            for ev in r.get("events", []):
                if ev.get("type") == "tool" and ev.get("data", {}).get("sources"):
                    sources = ev["data"]["sources"]
            print(f"引用溯源：{sources}")

    def scenario2_hitl(self) -> None:
        _sep("场景 2：HITL 人工确认 → resume 恢复")
        session_id = f"hitl-demo-{uuid.uuid4().hex[:8]}"
        r = self._post(
            "/api/chat",
            {
                "message": "帮我查一下数据库里有多少个会话（需要确认才能执行）",
                "use_rag": False,
                "use_search": False,
                "session_id": session_id,
            },
        )
        pending = r.get("hitl_pending")
        if not pending:
            print("未触发人工确认（服务端 HITL 未启用，或该动作被开关豁免）——跳过本场景。")
            return
        _dump("系统暂停，等待确认", pending)
        resume = self._post(
            "/api/chat",
            {
                "message": "",
                "use_rag": False,
                "use_search": False,
                "session_id": session_id,
                "resume": "confirmed",
            },
        )
        print("\n用户选择：confirmed")
        print(f"最终回答：{resume.get('answer', '')[:200]}")

    def scenario3_task_agent(self) -> None:
        _sep("场景 3：task-agent 长任务（知识库 + 计算）")
        r = self._post(
            "/api/agent-tasks/run",
            {"goal": "介绍一下公司（知识库）并计算 1 到 100 所有质数的和"},
        )
        if r.get("status") == "awaiting_confirm":
            _dump("HITL：等待确认下一步", r.get("pending"))
            sid = r["session_id"]
            r = self._post(
                "/api/agent-tasks/confirm",
                {"session_id": sid, "verb": "proceed"},
            )
        _dump("任务结果", r)
        print(f"\n最终交付：{(r.get('final_answer') or '')[:300]}")

    def scenario4_time_travel(self) -> None:
        _sep("场景 4：Time Travel（checkpoint 历史，可回退/分叉）")
        r = self._post("/api/agent-tasks/history", {"session_id": "demo-task", "limit": 5})
        hist = r.get("history") or []
        print(f"共 {len(hist)} 条 checkpoint：")
        for h in hist[:5]:
            print(
                f"  - {h.get('created_at', '?')}  next={h.get('next')}  "
                f"interrupted={h.get('interrupted')}  summary={(h.get('summary') or '')[:40]}"
            )
        if hist:
            print("\n恢复接口：/api/agent-tasks/run 传 checkpoint_id 即从该点分叉新分支。")

    def run(self, scenario: int | None) -> None:
        if scenario in (None, 1):
            self.scenario1_rag()
        if scenario in (None, 2):
            self.scenario2_hitl()
        if scenario in (None, 3):
            self.scenario3_task_agent()
        if scenario in (None, 4):
            self.scenario4_time_travel()


def main() -> None:
    ap = argparse.ArgumentParser(description="演示录屏脚本（需本地服务运行中）")
    ap.add_argument("--base", default="http://localhost:8000", help="后端地址")
    ap.add_argument("--scenario", type=int, choices=[1, 2, 3, 4], default=None, help="只跑单个场景")
    args = ap.parse_args()
    Showcase(args.base).run(args.scenario)


if __name__ == "__main__":
    main()
