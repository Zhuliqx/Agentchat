"""Agent 模型调用中间件（LangChain AgentMiddleware）。

通过 `awrap_model_call` 责任链在**每次模型调用**外层统一注入：
- 超时：防止单个模型调用卡死（asyncio 超时中断）
- 日志：记录每次模型调用耗时，便于观测

> 重试职责不在此层：LLM 客户端已自带网络层重试（`LLM_MAX_RETRIES`），
> 子 Agent 整体失败由 `agent_to_tool` 重试（`SUBAGENT_RETRIES`）。
> 三层重试叠加会指数放大失败请求数，故本中间件只做「超时 + 日志」。

用法：作为 `create_agent(..., middleware=[resilience_middleware()])` 传入，
supervisor 与各子 Agent 共享同一套容错策略。
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
