"""任务 Agent 配置（替代原宿主 app.config 的硬依赖）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskAgentConfig:
    """自主任务 Agent 运行配置。

    与宿主环境变量的对应关系（由宿主适配器负责映射）：
    - mode        ← TASK_AGENT_MODE
    - hitl        ← TASK_AGENT_HITL
    - max_retries ← TASK_AGENT_MAX_RETRIES
    - llm_timeout / llm_max_retries ← 宿主 LLM_TIMEOUT / LLM_MAX_RETRIES（节点级重试策略）
    """

    mode: str = "replan"          # replan=每步动态重规划（默认） / fixed=一次性计划
    hitl: bool = True             # 节点级人工确认（依赖 checkpointer，无则自动降级全自主）
    max_retries: int = 2          # verify 容错：单个子任务失败后自检的最大重试次数
    max_steps: int = 8            # replan 模式步数上限（防循环）
    llm_timeout: float = 60.0     # 单次 LLM 调用超时（节点级 timeout 用）
    llm_max_retries: int = 2      # 节点级瞬时错误重试次数

    def __post_init__(self) -> None:
        if self.mode not in ("replan", "fixed"):
            raise ValueError(f"未知任务模式: {self.mode!r}（支持 replan / fixed）")
        if self.max_retries < 0 or self.max_steps <= 0:
            raise ValueError("max_retries >= 0 且 max_steps > 0")
