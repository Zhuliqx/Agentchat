"""可观测适配：把 on_event 接到控制台 / Langfuse（可选 extra）。"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

EventSink = Callable[[str, dict], None]


def console_event_sink() -> EventSink:
    """控制台事件输出（零依赖）。"""

    def sink(kind: str, data: dict) -> None:
        print(f"[task-agent:{kind}] {data}")

    return sink


def langfuse_event_sink() -> EventSink:
    """Langfuse span 上报（需 `task-agent[observability]`）。

    未安装 / 未配置时自动降级为控制台输出，不抛错。
    """
    try:
        from langfuse import Langfuse

        lf = Langfuse()

        def sink(kind: str, data: dict) -> None:
            try:
                if lf.get_current_observation_id() is None:
                    return  # 无活动 trace 上下文，跳过以避免 Langfuse 噪音日志
                with lf.start_as_current_observation(
                    name=f"task_agent.{kind}", type="SPAN", input=data
                ):
                    pass
            except Exception:  # noqa: BLE001 - 观测失败不影响执行
                logger.debug("langfuse span 跳过: %s", kind, exc_info=True)

        return sink
    except Exception:  # noqa: BLE001 - 依赖缺失降级
        logger.warning("langfuse 未安装或未配置，事件降级为控制台输出")
        return console_event_sink()
