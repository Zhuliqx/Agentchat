"""自定义事件循环工厂（Windows 兼容）。

uvicorn 在 Windows 默认使用 ProactorEventLoop，而 psycopg 异步模式
（langgraph-checkpoint-postgres）需要 SelectorEventLoop。
通过 uvicorn 的 `loop` 参数传入本工厂，强制使用 SelectorEventLoop。

用法:
    uvicorn.run(..., loop="app.event_loop:selector_loop_factory")
"""
from __future__ import annotations

import asyncio
import sys


def selector_loop_factory(use_subprocess: bool = False):
    """返回适用于当前平台的事件循环。

    Windows: SelectorEventLoop（psycopg 异步必需）
    其他平台: 默认事件循环
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
