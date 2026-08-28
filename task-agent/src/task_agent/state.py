"""自主任务 Agent 的图状态（TaskState）。"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict


def _append_findings(a: Optional[list[str]], b: Optional[list[str]]) -> list[str]:
    """findings 的 reducer：以增量方式合并状态（节点返回新增片段，而非每次回写全量）。"""
    return (a or []) + (b or [])


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
    # [verify 容错] 当前子任务失败后的重试次数(成功即归零,上限 MAX_RETRIES)
    retries: Optional[int]
    # [HITL 内部] 计划确认结果: proceed / edit / skip(仅节点内路由用,不下发)
    _confirm_verb: Optional[str]
    # 共有
    findings: Annotated[list[str], _append_findings]  # 已完成结果(节点只回增量,reducer 拼接)
    final_answer: Optional[str]            # 最终交付
