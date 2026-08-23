"""自主任务 Agent 的图状态（TaskState）。

一期 fixed 与二期 replan 共用同一状态结构；两种模式各自使用其子集字段。
"""
from __future__ import annotations

from typing import Optional, TypedDict


class TaskState(TypedDict):
    """Plan→Execute→Final(一期) 与 Replan→Execute→Check(二期) 循环的共享状态。"""

    goal: str                              # 用户目标
    # [fixed 一期] 一次性计划：子任务列表 + 当前索引
    plan: Optional[list[dict]]             # [{id, desc, status, result}]
    current_idx: Optional[int]             # 当前子任务索引(fixed 用)
    # [replan 二期] 每步动态：当前动作 + 步数
    current_action: Optional[str]          # 下一步执行的动作(replan 用)
    step: Optional[int]                    # 已完成步数(replan 用)
    done: Optional[bool]                   # check/replan 判定是否完成
    expected_source: Optional[str]         # replan 标注的信息来源(kb/db/web/code/default)
    # 共有
    findings: Optional[list[str]]          # 已完成结果(累加截断)
    final_answer: Optional[str]            # 最终交付