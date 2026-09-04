"""Windows 兼容的后端启动入口。

uvicorn 默认在 Windows 使用 ProactorEventLoop，而 psycopg 异步模式
（langgraph-checkpoint-postgres 的 AsyncPostgresSaver）需要 SelectorEventLoop。
事件循环策略已在 `app.main` 顶部设置（app.main 模块加载时生效），本入口
通过 uvicorn 的 `loop` 参数传入 SelectorEventLoop 工厂，双保险。

用法:
    python run.py            # 监听 settings.host / settings.port（默认 127.0.0.1:8000）
                             # 生产/容器部署请通过 .env 设 HOST=0.0.0.0
"""
from __future__ import annotations

import sys
from pathlib import Path

# 保证能找到 app 包（无论从哪个目录启动）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        loop="app.event_loop:selector_loop_factory",  # Windows 下强制 SelectorEventLoop
    )
