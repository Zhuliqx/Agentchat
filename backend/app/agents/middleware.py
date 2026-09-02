"""Agent 模型调用中间件（LangChain AgentMiddleware）。

通过 `awrap_model_call` 责任链在**每次模型调用**外层统一注入：
- 超时：防止单个模型调用卡死（asyncio 超时中断）
- 日志：记录每次模型调用耗时，便于观测

> 重试职责不在此层：LLM 客户端已自带网络层重试（`LLM_MAX_RETRIES`），
> 子 Agent 整体失败由 `agent_to_tool` 重试（`SUBAGENT_RETRIES`）。
> 三层重试叠加会指数放大失败请求数，故本中间件只做「超时 + 日志」。

用法：作为 `create_agent(..., middleware=[resilience_middleware()])` 传入，
supervisor 与各子 Agent 共享同一套容错策略。

重试层级（每层只管一种失败面，新增重试前先对照，避免叠成指数放大）：
1. LLM 客户端网络重试：`LLM_MAX_RETRIES`（ChatOpenAI 等 SDK 内建，管 5xx/限流/连接）；
2. 本中间件（resilience）：只做超时 + 日志，**不重试**——职责是防卡死而非重试；
3. agent_to_tool 子 Agent 整体重试：`SUBAGENT_RETRIES`（子 Agent 调用失败时重跑）；
4. task-agent `_HostExecutor`：空答案双次尝试（防上游偶发空响应，不重复计步）；
5. task-agent 节点级：`llm_text` 空响应重试、replan/verify 解析级重试、
   图节点 `RetryPolicy` + `error_handler`（瞬时错误重试，耗尽后降级）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_resilience_middleware: Any | None = None


def resilience_middleware(timeout: float | None = None) -> Any:
    """返回「超时 / 日志」的模型调用中间件（全局单例，配置一致性 + 复用）。"""
    global _resilience_middleware
    if _resilience_middleware is None:
        from langchain.agents.middleware import AgentMiddleware

        class ModelResilienceMiddleware(AgentMiddleware[Any, Any, Any]):
            """统一「超时 / 日志」的模型调用中间件。"""

            @property
            def name(self) -> str:
                return "model_resilience"

            def __init__(self, timeout: float | None = None) -> None:
                # 默认复用 LLM 超时配置
                self.timeout = timeout if timeout is not None else settings.llm_timeout

            async def awrap_model_call(self, request: Any, handler: Any) -> Any:
                """责任链包装：超时 + 计时日志（不做重试，避免多层重试叠加）。"""
                start = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(handler(request), timeout=self.timeout)
                    logger.debug("模型调用成功，耗时 %.2fs", time.perf_counter() - start)
                    return resp
                except asyncio.TimeoutError:
                    elapsed = time.perf_counter() - start
                    logger.warning("模型调用超时（%.2fs > %.0fs）", elapsed, self.timeout)
                    raise TimeoutError(f"模型调用超过 {self.timeout:.0f}s 超时（middleware）")
                except Exception:
                    elapsed = time.perf_counter() - start
                    logger.warning("模型调用失败（耗时 %.2fs）", elapsed)
                    raise

        _resilience_middleware = ModelResilienceMiddleware(timeout=timeout)
    return _resilience_middleware


def build_supervisor_middlewares(history_summary_model: Any) -> list[Any]:
    """组装 Supervisor 图的全部中间件（摘要压缩 / 调用上限 / 超时日志）。

    统一以 ``list[Any]`` 返回：各中间件的泛型 StateT/ContextT 不同
    （如 ToolCallLimitState vs ModelCallLimitState），若让类型推断产生
    具体泛型联合，会与 ``create_agent`` 期望的
    ``Sequence[AgentMiddleware[StateT, ContextT, Any]]`` 不兼容（纯类型问题）。
    ``agent_max_*_calls=0`` 表示不挂载对应上限。
    """
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    from app.agents.history_summary import build_history_summary_middleware

    mws: list[Any] = []
    summary_mw = build_history_summary_middleware(model=history_summary_model)
    if summary_mw is not None:
        mws.append(summary_mw)
    if settings.agent_max_tool_calls > 0:
        mws.append(
            ToolCallLimitMiddleware(
                run_limit=settings.agent_max_tool_calls,
                exit_behavior="continue",
            )
        )
    if settings.agent_max_model_calls > 0:
        mws.append(
            ModelCallLimitMiddleware(
                run_limit=settings.agent_max_model_calls,
                exit_behavior="end",
            )
        )
    mws.append(resilience_middleware())
    return mws
