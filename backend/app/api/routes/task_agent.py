"""自主任务 Agent 的 API：接收模糊目标，返回 plan + findings + final_answer。

节点级 HITL：开启 task_agent_hitl 后，首次操作前会中断返回 awaiting_confirm，
需调用 /agent-tasks/confirm 提交决策(proceed/edit/skip)恢复执行(target thread_id)。

事件流：POST /api/agent-tasks/run/stream 以 SSE 实时推送执行过程事件
（plan/replan/execute/check/verify/hitl/final），前端可展示"正在做什么"。

图由宿主适配器（app.agents.task_agent_adapter）注入 LLM / Checkpointer / 执行器构建；
自主任务引擎本体为独立包 task-agent。
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.task_agent_adapter import build_host_task_agent, list_task_history
from app.api.deps import get_current_user_id

router = APIRouter()


class AgentTaskRun(BaseModel):
    goal: str = Field("", max_length=2000, description="用户目标(新任务必填; 分叉/重放时可空)")
    session_id: str | None = None  # 为空则新建任务会话（checkpoint thread_id）
    checkpoint_id: str | None = None  # Time Travel: 从历史 checkpoint 分叉(带goal)或重放(无goal)
    checkpoint_ns: str = Field("", description="from history 返回；顶层图通常为空串")


class AgentTaskConfirm(BaseModel):
    session_id: str = Field(..., description="任务会话(thread_id)")
    verb: str = Field("proceed", description="决策: proceed(执行) / edit(改用动作) / skip(跳过)")
    action: str | None = Field(None, description="verb=edit 时的替换动作")
    source: str | None = Field(None, description="verb=edit 时的信息来源(kb/db/web/code)")


def _pack(result: dict, thread: str) -> dict:
    """统一封装结果；若处于 HITL 中断则返回 awaiting_confirm。"""
    interrupts = result.get("__interrupt__")
    if interrupts:
        return {"session_id": thread, "status": "awaiting_confirm",
                "pending": getattr(interrupts[0], "value", None)}
    return {
        "session_id": thread,
        "status": "done",
        "plan": result.get("plan"),
        "findings": result.get("findings") or [],
        "final_answer": result.get("final_answer", ""),
    }


def _prepare_run_input(graph, req: AgentTaskRun, thread: str) -> tuple[dict | None, dict]:
    """组装图输入与 config（run / stream 共用；含 Time Travel 分叉/重放）。"""
    config = {"configurable": {"thread_id": thread}}
    if req.checkpoint_id:
        config["configurable"]["checkpoint_id"] = req.checkpoint_id
        if req.goal:
            # Time Travel 分叉：从历史点改用新 goal(update_state 建新分支)后继续执行。
            # update_state 需完整 config(含 checkpoint_ns)，从 history 返回中带回。
            try:
                fc = {
                    "configurable": {
                        "thread_id": thread,
                        "checkpoint_id": req.checkpoint_id,
                        "checkpoint_ns": req.checkpoint_ns or "",
                    }
                }
                # 必须用 update_state 返回的 config 继续（新分支的 checkpoint_id）
                cfg = graph.update_state(fc, {"goal": req.goal})
            except Exception as exc:
                raise HTTPException(400, f"分叉失败: {exc}") from exc
            return None, cfg
        # Time Travel 重放：从历史点继续(沿用已存 state)
        return None, config
    if not req.goal:
        raise HTTPException(400, "新任务需提供 goal")
    return {"goal": req.goal}, config


@router.post("/agent-tasks/run")
async def run_agent_task(req: AgentTaskRun, user_id: str = Depends(get_current_user_id)) -> dict:
    graph = build_host_task_agent()
    thread = req.session_id or f"task-{uuid.uuid4().hex[:12]}"
    input_data, cfg = _prepare_run_input(graph, req, thread)
    try:
        result = await graph.ainvoke(input_data, config=cfg)
    except Exception as exc:
        raise HTTPException(500, f"任务执行失败: {exc}") from exc
    return _pack(result, thread)


@router.post("/agent-tasks/run/stream")
async def run_agent_task_stream(
    req: AgentTaskRun, user_id: str = Depends(get_current_user_id)
) -> StreamingResponse:
    """SSE 事件流：实时推送任务执行过程，最后推 result（含 HITL awaiting_confirm）。"""
    thread = req.session_id or f"task-{uuid.uuid4().hex[:12]}"
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(kind: str, data: dict) -> None:
        # 包内回调为同步签名；在事件循环内调度推送
        asyncio.ensure_future(queue.put({"type": "event", "kind": kind, "data": data}))

    graph = build_host_task_agent(on_event=on_event)
    input_data, cfg = _prepare_run_input(graph, req, thread)

    async def produce() -> None:
        try:
            result = await graph.ainvoke(input_data, config=cfg)
            await queue.put({"type": "result", "data": _pack(result, thread)})
        except Exception as exc:  # noqa: BLE001 - SSE 内推送错误帧
            await queue.put({"type": "error", "data": {"message": str(exc)}})
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


class AgentTaskHistory(BaseModel):
    session_id: str = Field(..., description="任务会话(thread_id)")
    limit: int = Field(30, ge=1, le=100, description="返回历史条数上限")


@router.post("/agent-tasks/history")
async def agent_task_history(req: AgentTaskHistory, user_id: str = Depends(get_current_user_id)) -> dict:
    """Time Travel：列出线程的 checkpoint 历史(新→旧)，每条含 checkpoint_id 可回退/分叉。"""
    graph = build_host_task_agent()
    items = await list_task_history(graph, req.session_id, req.limit)
    return {"session_id": req.session_id, "history": items}


@router.post("/agent-tasks/confirm")
async def confirm_agent_task(req: AgentTaskConfirm, user_id: str = Depends(get_current_user_id)) -> dict:
    """HITL 恢复：提交决策，从上次 interrupt 处继续执行（同一 thread_id）。"""
    graph = build_host_task_agent()
    decision = {"verb": req.verb, "action": req.action, "source": req.source}
    try:
        result = await graph.ainvoke(
            Command(resume=decision),
            config={"configurable": {"thread_id": req.session_id}},
        )
    except Exception as exc:
        raise HTTPException(500, f"恢复失败: {exc}") from exc
    return _pack(result, req.session_id)
