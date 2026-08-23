"""自主任务 Agent 的图状态（TaskState）。"""
from __future__ import annotations

from typing import TypedDict


class TaskState(TypedDict):
    """Plan→Execute→Final 循环的共享状态。"""

    goal: str                # 用户目标
    plan: list[dict]         # [{id, desc, status, result}]，status: pending/done/failed
    current_idx: int         # 当前执行的子任务索引
    findings: list[str]      # 各子任务结果
    final_answer: str        # 最终交付