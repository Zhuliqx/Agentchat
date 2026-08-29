"""自主任务 Agent 的图状态（TaskState）。"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict


def _append_findings(a: Optional[list[str]], b: object) -> list[str]:
    """findings 的 reducer：以增量方式合并状态（节点返回新增片段，而非每次回写全量）。

    特殊值 `{"_replace": [...]}`：超预算压缩时整体替换（见 nodes._append_finding）。
    """
    if isinstance(b, dict) and b.get("_replace") is not None:
        return list(b["_replace"])
    return (a or []) + (b if isinstance(b, list) else [])


class TaskState(TypedDict):
    """Plan→Execute→Final 与 Replan→Execute→Check 循环的共享状态。"""

    goal: str                              # 用户目标
    # [fixed] 一次性计划：子任务列表 + 当前索引
    plan: Optional[list[dict]]             # [{id, desc, status, result}]
    current_idx: Optional[int]             # 当前子任务索引(fixed 用)
    # [replan] 每步动态：当前动作 + 步数
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
    findings_summary: Optional[str]                   # 超预算压缩后的历史摘要(默认空)
    final_answer: Optional[str]            # 最终交付
