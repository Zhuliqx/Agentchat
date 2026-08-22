"""聊天接口：对接 LangGraph 多 Agent 编排。

提供两个端点：
- POST /api/chat        非流式：一次性返回答案与事件（兼容旧客户端）
- POST /api/chat/stream 流式（SSE）：事件实时推送 + 答案增量，前端打字机展示

同步 DB 调用统一放进线程池（anyio.to_thread），避免阻塞事件循环——
这是\"不改 ORM 为异步\"的轻量方案。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

from app.config import settings
from app.agents.graph import (
    AgentTimeoutError,
    get_supervisor_graph,
    run_agent,
    stream_agent,
)
from app.agents.tools import get_recent_rag_sources
from app.api.deps import get_current_user_id
from app.db import postgres
from app.db.memory_store import get_checkpointer
from app.schemas.chat import AgentEvent, ChatRequest, ChatResponse
from app.rag.prompt_injection import detect_injection, detect_leak

router = APIRouter()

# HITL 检查结果短路缓存：同一会话短时间内已确认无 pending interrupt 则跳过，
# 避免每个普通请求都 aget_state 恢复完整图状态（隐藏开销）。
# 安全性：interrupt 只在上轮工具调用时产生且需用户处理；5s 内重复新消息基本不会携带新中断，
# 而中断产生后的下一次请求（用户操作后）TTL 已过，仍会重新检查。
_HITL_CHECK_TTL = 5.0
_hitl_checked: dict[str, float] = {}  # session_id -> monotonic ts


# ---------------- 会话准备（同步 DB，放线程池） ----------------

def _ensure_resume_checkpoint_conflict(
    resume: str | None, checkpoint_id: str | None
) -> None:
    """resume（HITL 恢复）与 checkpoint_id（Time Travel 分叉）互斥。"""
    if resume is not None and checkpoint_id is not None:
        raise HTTPException(400, "resume 与 checkpoint_id 不能同时使用")


def _prepare_session(session_id: str | None, user_id: str) -> str:
    """确保会话存在并返回 session_id（归属当前用户）。同步函数，调用方用线程池执行。"""
    if session_id:
        if not postgres.get_owned_session(session_id, user_id):
            raise HTTPException(404, "会话不存在")
    else:
        session = postgres.create_session(user_id=user_id)
        session_id = session.id
    return session_id


async def _check_pending_interrupt(session_id: str, use_rag: bool, use_search: bool) -> None:
    """HITL 防护：复用已有会话发普通新消息时，若 Checkpointer 中存在未完成的人工确认
    （pending interrupt），直接拒绝并给出明确提示——否则把含"未完成 tool_calls"的
    历史发给 LLM 会触发 400（tool_calls 缺少对应 tool 消息）。

    带 TTL 短路缓存：短时间重复请求同一会话时跳过图状态恢复（提速）。
    """
    now = time.monotonic()
    last = _hitl_checked.get(session_id)
    if last is not None and now - last < _HITL_CHECK_TTL:
        return
    cp = get_checkpointer()
    if cp is None:
        return
    try:
        graph = get_supervisor_graph(use_rag=use_rag, use_search=use_search)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        for task in getattr(state, "tasks", []):
            if getattr(task, "interrupts", None):
                raise HTTPException(
                    409,
                    "该会话存在未完成的人工确认：请先点击上一条的【确认执行/取消】按钮，"
                    "或新建会话继续。",
                )
        # 确认无 pending interrupt → 记录短路缓存
        _hitl_checked[session_id] = now
    except HTTPException:
        raise
    except Exception:
        pass  # 状态读取失败则放行，不阻塞正常对话


async def _prepare_context(req: ChatRequest, user_id: str) -> tuple[str, str | None]:
    """会话准备 + HITL 防护 + 保存用户消息（resume 时跳过）。

    返回 (session_id, 用户消息 id)；resume 场景用户消息已存在，返回 None。
    """
    # Prompt 注入防护：新消息（非 resume）query 含注入指令 → 明确拒绝
    if req.resume is None:
        detected, pats = detect_injection(req.message)
        if detected:
            raise HTTPException(
                400, f"检测到可疑指令（{", ".join(pats)}），请重新表述问题。"
            )
    user_msg_id: str | None = None
    try:
        session_id = await anyio.to_thread.run_sync(
            _prepare_session, req.session_id, user_id
        )
    except HTTPException:
        raise

    # HITL 防护：复用会话且存在未完成的人工确认时，给出明确提示而非 400。
    # Time Travel 分叉（checkpoint_id）从历史点开新分支，与当前 pending 无关，跳过。
    if req.resume is None:
        if req.checkpoint_id is None:
            await _check_pending_interrupt(session_id, req.use_rag, req.use_search)
        # 保存用户消息（HITL 恢复时消息已在历史中，跳过避免重复）
        msg = await anyio.to_thread.run_sync(
            postgres.add_message, session_id, "user", req.message
        )
        user_msg_id = msg.id
    return session_id, user_msg_id


async def _save_assistant_if_final(
    session_id: str, result: dict, user_id: str
) -> str | None:
    """HITL 等待确认时不保存空答案；否则保存 assistant 消息并返回其 id。

    保存时把本轮 RAG 检索命中的来源（引用溯源）一起持久化，切换会话后
    重新加载历史仍能看到来源。产生人工确认（pending interrupt）时清除
    该会话的 TTL 短路缓存：否则 _check_pending_interrupt 的 5s 短路会让
    用户随后的普通新消息跳过检查，把含"未完成 tool_calls"的历史发给 LLM
    触发 400，而非明确的 409。
    """
    if result.get("hitl_pending") is not None:
        _hitl_checked.pop(session_id, None)
        return None
    # 输出侧泄露检测：回答含系统提示词片段/密钥模式 → 告警（不改回答）
    if settings.injection_output_filter:
        leaked, kinds = detect_leak(result.get("answer", ""))
        if leaked:
            logger.warning("检测到回答泄露信号 session=%s kinds=%s", session_id, kinds)
    sources = get_recent_rag_sources(user_id or "default") or None
    msg = await anyio.to_thread.run_sync(
        postgres.add_message, session_id, "assistant", result["answer"], sources
    )
    return msg.id


# ---------------- 非流式 ----------------

@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest, user_id: str = Depends(get_current_user_id)
) -> ChatResponse:
    """单轮对话：保存历史，运行多 Agent，返回答案与执行事件。"""
    _ensure_resume_checkpoint_conflict(req.resume, req.checkpoint_id)
    session_id, _ = await _prepare_context(req, user_id)

    # 事件收集
    events: list[AgentEvent] = []

    async def on_event(ev: dict):
        events.append(
            AgentEvent(type=ev["type"], content=ev.get("content", ""), data=ev.get("data", {}))
        )

    # 运行多 Agent（session_id 作为 Checkpointer thread_id，user_id 归属长期记忆）
    try:
        result = await run_agent(
            question=req.message,
            use_rag=req.use_rag,
            use_search=req.use_search,
            use_memory=req.use_memory,
            session_id=session_id,
            user_id=req.user_id or user_id,
            resume=req.resume,
            checkpoint_id=req.checkpoint_id,
            on_event=on_event,
        )
    except AgentTimeoutError:
        raise HTTPException(504, "处理超时，请重试或简化问题")

    hitl_pending = result.get("hitl_pending")
    await _save_assistant_if_final(session_id, result, user_id)

    events.append(AgentEvent(type="message", content=result["answer"]))
    return ChatResponse(
        session_id=session_id,
        answer=result["answer"],
        events=events,
        used_agents=result["used_agents"],
        hitl_pending=hitl_pending,
    )


# ---------------- 流式（SSE） ----------------

def _sse_frame(data: dict) -> str:
    """编码为 SSE data 帧（UTF-8）。"""
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


@router.post("/stream")
async def chat_stream(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
    """SSE 流式对话：事件实时推送，最终推送 message（完整答案）帧。

    前端可逐事件渲染 Agent 调用过程，答案出来后打字机展示。
    """
    _ensure_resume_checkpoint_conflict(req.resume, req.checkpoint_id)
    session_id, user_msg_id = await _prepare_context(req, user_id)

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict):
        data = ev.get("data", {})
        # interrupt 事件需携带 session_id，前端 resume 时要用同一 thread_id
        if ev["type"] == "interrupt" and isinstance(data, dict):
            data = {**data, "session_id": session_id}
        await queue.put(
            {"type": ev["type"], "content": ev.get("content", ""), "data": data}
        )

    async def on_token(text: str):
        # token 级流式：逐段推送 supervisor 输出的增量文本
        await queue.put({"type": "token", "content": text})

    async def produce():
        """运行 Agent（token 流式）并把事件/结果送入队列。"""
        try:
            # 先推用户消息 id（供前端删除/定位）
            if user_msg_id:
                await queue.put(
                    {
                        "type": "meta",
                        "content": "",
                        "data": {"user_message_id": user_msg_id},
                    }
                )
            result = await stream_agent(
                question=req.message,
                use_rag=req.use_rag,
                use_search=req.use_search,
                use_memory=req.use_memory,
                session_id=session_id,
                user_id=req.user_id or user_id,
                resume=req.resume,
                checkpoint_id=req.checkpoint_id,
                on_event=on_event,
                on_token=on_token,
            )
            # HITL：等待用户确认时不保存空答案、不发 message 帧
            #（interrupt 事件已由 on_event 推送，待用户 resume 后继续）
            assistant_id = await _save_assistant_if_final(session_id, result, user_id)
            if assistant_id is None:
                return
            await queue.put(
                {
                    "type": "message",
                    "content": result["answer"],
                    "data": {
                        "used_agents": result["used_agents"],
                        "session_id": session_id,
                        "message_id": assistant_id,
                    },
                }
            )
        except AgentTimeoutError:
            await queue.put({"type": "error", "content": "处理超时，请重试或简化问题"})
        except Exception as exc:  # 避免连接被意外中断
            logger.exception("chat_stream 处理失败")
            await queue.put({"type": "error", "content": f"处理失败: {exc}"})
        finally:
            await queue.put(None)  # 结束哨兵

    async def event_stream():
        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse_frame(item)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
