"""测试辅助：FakeLLM 与编排测试的 monkeypatch 工具。

FakeLLM 按"消息历史最后一条类型"决策：
- 最后一条是 ToolMessage → 返回文本（工具执行后的最终回答）；
- 配置了 tool_calls → 返回工具调用（触发 supervisor 路由）；
- 否则 → 返回文本（直接回答）。

用于驱动 supervisor 图（create_agent）而不依赖真实 DeepSeek。
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class FakeLLM(BaseChatModel):
    """按消息历史返回预设响应的假 LLM。"""

    text: str = "最终回答"
    tool_calls: list[dict] = []  # 空 = 不调工具（直接回答）

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs: Any):
        """Fake 模型不真正绑定工具 schema；返回自身即可（create_agent 会调用）。"""
        return self

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> ChatResult:
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            msg = AIMessage(content=self.text)
        elif self.tool_calls:
            msg = AIMessage(content="", tool_calls=self.tool_calls)
        else:
            msg = AIMessage(content=self.text)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        result = self._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        msg = result.generations[0].message
        if msg.tool_calls:
            chunks = [
                {
                    "name": tc.get("name", ""),
                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    "id": tc.get("id", "call_fake"),
                    "index": i,
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
            chunk = AIMessageChunk(content="", tool_call_chunks=chunks)
        else:
            chunk = AIMessageChunk(content=msg.content)
        yield ChatGenerationChunk(message=chunk)


class _FakeMcpManager:
    """空 MCP manager：避免 build_mcp_agent 连真实 MCP。"""

    def get_langchain_tools(self) -> list[Any]:
        return []


def patch_llms(
    monkeypatch: Any,
    supervisor: FakeLLM,
    subagent: Optional[FakeLLM] = None,
) -> None:
    """注入 FakeLLM 并隔离 MCP，清空图缓存。"""
    from app.agents import graph as graph_mod
    from app.agents.tools import code_tool, mcp_tool, rag_tool

    sub = subagent or supervisor
    monkeypatch.setattr(graph_mod, "get_llm", lambda kind="main": supervisor)
    # 工具族拆分后，各子模块各自持有 get_llm/get_mcp_manager 引用，需分别替换
    monkeypatch.setattr(rag_tool, "get_llm", lambda kind="light": sub)
    monkeypatch.setattr(code_tool, "get_llm", lambda kind="light": sub)
    monkeypatch.setattr(mcp_tool, "get_llm", lambda kind="light": sub)
    monkeypatch.setattr(mcp_tool, "get_mcp_manager", lambda: _FakeMcpManager())
    # 关闭图执行缓存，避免 FakeLLM 相同输入被缓存跳过
    from app.config import settings

    monkeypatch.setattr(settings, "agent_cache_enabled", False)
    graph_mod.clear_graph_cache()


# ---------------- 集成测试基础设施（DB/向量库可用性与最终一致性等待） ----------------
# 各集成测试文件统一从这里取跳过判据与 Milvus 等待工具，避免重复实现。


def postgres_available() -> bool:
    """Postgres 是否可达（仅需 DB 的集成用例统一跳过判据）。"""
    try:
        from sqlalchemy import text

        from app.db.postgres import SessionLocal

        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def db_available() -> bool:
    """Postgres + Milvus 是否可达（RAG 类集成用例统一跳过判据）。"""
    try:
        from app.rag.vector_store import _client

        if not postgres_available():
            return False
        _client().list_collections()
        return True
    except Exception:  # noqa: BLE001
        return False


def milvus_source_rows(source: str) -> list[tuple[str, int]]:
    """查询 Milvus 中某 source 的全部 (doc_id, chunk_index)。"""
    from app.rag import vector_store

    escaped = source.replace("\\", "\\\\").replace('"', '\\"')
    res = vector_store._client().query(
        vector_store.settings.milvus_collection,
        filter=f'source == "{escaped}"',
        output_fields=["doc_id", "chunk_index"],
    )
    return [(r.get("doc_id"), int(r.get("chunk_index"))) for r in res]


def milvus_source_texts(source: str) -> list[str]:
    """查询 Milvus 中某 source 的全部块文本。"""
    from app.rag import vector_store

    escaped = source.replace("\\", "\\\\").replace('"', '\\"')
    res = vector_store._client().query(
        vector_store.settings.milvus_collection,
        filter=f'source == "{escaped}"',
        output_fields=["text"],
    )
    return [str(r.get("text") or "") for r in res]


def wait_milvus_visible(
    source: str, min_rows: int = 1, timeout: float = 20.0
) -> None:
    """等待该 source 的向量在 Milvus 中可查询（写入是最终一致的）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if len(milvus_source_rows(source)) >= min_rows:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
    raise AssertionError(
        f"Milvus 中未能在 {timeout:.0f}s 内看到该 source 的向量（最终一致性等待超时）"
    )


def wait_milvus_converged(
    source: str, want: list[tuple[str, int]], timeout: float = 20.0
) -> list[tuple[str, int]]:
    """轮询 Milvus 直到 (doc_id, chunk_index) 集合收敛（最终一致性）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = milvus_source_rows(source)
        if sorted(got) == sorted(want):
            return got
        time.sleep(1.0)
    return milvus_source_rows(source)
