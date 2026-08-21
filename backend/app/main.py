"""FastAPI 应用入口。

启动方式:
    cd backend
    python run.py

前端页面由本服务托管，访问 http://localhost:8000 即可。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Windows 上 psycopg 异步模式需要 SelectorEventLoop（默认 Proactor 不兼容）
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# 前端 dist 路径直接基于项目根计算（见下方 APP_DIST）

from app.config import PROJECT_ROOT, settings

# 按配置设置根日志级别（便于生产观测模型耗时等 debug 信息）
logging.getLogger().setLevel(
    getattr(logging, settings.log_level.upper(), logging.INFO)
)

# 模型离线加载：HF 网络不可达时避免 huggingface_hub 联网 HEAD 检查卡住重试。
# 注意：huggingface_hub 的 HF_HUB_OFFLINE 常量在 import 时读取并缓存，
# 若其他模块（如 langchain/ingestion）先于本文件 import 了它，仅设置 env 不生效，
# 因此这里同时强制覆盖 env 与已缓存的常量，并在加载模型时传 local_files_only=True。
if settings.hf_offline:
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        import huggingface_hub.constants as _hf_constants

        _hf_constants.HF_HUB_OFFLINE = True
    except Exception:  # huggingface_hub 未安装/不可用时忽略
        pass

from app.api.routes import (
    admin,
    auth,
    chat,
    health,
    memory,
    models,
    rag,
    search,
    sessions,
    tasks,
)
from app.db.memory_store import (
    cleanup_stale_checkpoints,
    close_checkpointer,
    close_store,
    init_checkpointer,
    init_store,
)
from app.db.postgres import init_db
from app.mcp_integration.client import get_mcp_manager
from app.rag.vector_store import ensure_vector_store
from app.scheduler import scheduler_loop

APP_DIST = PROJECT_ROOT / "frontend-v2" / "dist"


def _warmup_sync() -> None:
    """后台线程预热（模型 + Supervisor 图），避免阻塞首个请求。

    - rerank/embedding 首次加载较慢；
    - Supervisor 图（含各子 Agent）在延迟导入方案下首次对话才构建，后台预热避免首轮等待；
      图由 _graph_cache 缓存，预热失败则请求时按需构建（仅告警）。
    """
    try:
        if settings.rerank_enabled:
            from app.rag.rerank import _get_reranker

            _get_reranker()
            logger.info("rerank 模型预热完成（%s）", settings.rerank_model)
        from app.rag.embedding import get_embedder

        get_embedder()
        logger.info("embedding 模型预热完成（%s）", settings.embedding_model)
    except Exception as exc:
        logger.warning("模型预热失败（请求时将降级）: %s", exc)
    try:
        # 预热 default 用户的 BM25 索引（消除首次 RAG 检索的索引构建延迟）
        from app.rag.hybrid import _bm25_index, _docs_signature

        sig = _docs_signature("default")
        if sig[0] and sig[0] <= settings.bm25_max_docs:
            _bm25_index(sig, "default")
            logger.info("BM25 索引预热完成（%s 块）", sig[0])
    except Exception as exc:
        logger.warning("BM25 预热失败（请求时按需构建）: %s", exc)
    try:
        from app.agents.graph import get_supervisor_graph

        get_supervisor_graph()
        logger.info("Supervisor 图预热完成")
    except Exception as exc:
        logger.warning("图预热失败（请求时将按需构建）: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化数据库、向量库、Checkpointer，并拉起 MCP 服务器。"""
    # uvicorn 的 dictConfig 只给 uvicorn.* logger 配置 handler，会清空 root 的
    # handler 与 level（root 默认 WARNING，且无 handler 时 info 被 lastResort 丢弃）。
    # 这里在 lifespan 内（uvicorn 配置之后）补齐 root handler + level，保证可观测性。
    _root = logging.getLogger()
    if not _root.handlers:
        _handler = logging.StreamHandler()
        _handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _root.addHandler(_handler)
    _root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    # 运行时配置覆盖（DB app_settings 优先级高于 .env；管理后台可在线调整）
    from app.db.runtime_settings import load_runtime_settings

    load_runtime_settings()
    try:
        ensure_vector_store()
    except Exception as exc:  # 数据库未启动时不阻塞应用
        logger.warning("向量库初始化失败（请确认 Milvus 已启动）: %s", exc)

    # 初始化 LangGraph Checkpointer（短期/运行时状态持久化）
    try:
        await init_checkpointer()
    except Exception as exc:
        logger.warning("Checkpointer 初始化失败: %s", exc)

    # 初始化 LangGraph Store（长期记忆）
    try:
        await init_store()
    except Exception as exc:
        logger.warning("Store 初始化失败: %s", exc)

    # 清理孤儿 checkpoint（对应会话已删除的）
    cleanup_stale_checkpoints()

    # 启动 MCP 服务器（自建 stdio + 外部 http）
    try:
        connected = await get_mcp_manager().start_all()
        logger.info("MCP 已连接服务器: %s", connected)
    except Exception as exc:
        logger.warning("MCP 启动失败: %s", exc)

    # 后台预热 rerank 模型（不阻塞启动，避免首个 RAG 请求卡顿下载）
    threading.Thread(target=_warmup_sync, daemon=True).start()

    # 启动后台任务调度器（定时/批处理任务）
    scheduler_stop = asyncio.Event()
    scheduler_task = asyncio.create_task(scheduler_loop(scheduler_stop))

    yield

    scheduler_stop.set()
    scheduler_task.cancel()
    await get_mcp_manager().stop_all()
    await close_checkpointer()
    await close_store()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="基于 FastAPI + LangGraph + LangChain 的多 Agent 平台（RAG + MCP）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    """统一未捕获异常为 JSON（避免返回 HTML 500，前端 parseError 可读）。

    业务错误用 HTTPException（detail 字符串）按默认结构返回；
    这里只兜底意外异常，保证响应始终是统一 JSON 结构。
    """
    logger.error("未处理异常 %s: %s", type(exc).__name__, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "code": "internal_error"},
    )


app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

# 托管前端（最后挂载，作为兜底；"/" 返回 index.html）
class _NoCacheStaticFiles(StaticFiles):
    """前端静态文件禁用缓存：避免 VS Code 内置浏览器缓存旧版 CSS/JS，导致改动不生效。"""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


if APP_DIST.exists():
    # 托管新版前端 frontend-v2 的构建产物（Vue 3 + Vite + TS）
    app.mount(
        "/",
        _NoCacheStaticFiles(directory=APP_DIST, html=True),
        name="frontend",
    )
