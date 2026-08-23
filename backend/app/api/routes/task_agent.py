"""自主任务 Agent 的 API：接收模糊目标，返回 plan + findings + final_answer。

节点级 HITL：开启 task_agent_hitl 后，首次操作前会中断返回 awaiting_confirm，
需调用 /agent-tasks/confirm 提交决策(proceed/edit/skip)恢复执行(target thread_id)。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id
from app.task_agent.graph import get_task_agent, list_task_history

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


@router.post("/agent-tasks/run")
async def run_agent_task(req: AgentTaskRun, user_id: str = Depends(get_current_user_id)) -> dict:
    graph = get_task_agent()
    thread = req.session_id or f"task-{uuid.uuid4().hex[:12]}"
    config = {"configurable": {"thread_id": thread}}
    if req.checkpoint_id:
        config["configurable"]["checkpoint_id"] = req.checkpoint_id
        if req.goal:
            # Time Travel 分叉：从历史点改用新 goal(update_state 建新分支)后继续执行。
            # update_state 需完整 config(含 checkpoint_ns)，从 history 返回中带回。
            try:
                fc = {"configurable": {"thread_id": thread, "checkpoint_id": req.checkpoint_id,
                                       "checkpoint_ns": req.checkpoint_ns or ""}}
                input_data, cfg = None, graph.update_state(fc, {"goal": req.goal})
            except Exception as exc:
                raise HTTPException(400, f"分叉失败: {exc}") from exc
        else:
            # Time Travel 重放：从历史点继续(沿用已存 state)
            input_data, cfg = None, config
    else:
        if not req.goal:
            raise HTTPException(400, "新任务需提供 goal")
        input_data, cfg = {"goal": req.goal}, config
    try:
        result = await graph.ainvoke(input_data, config=cfg)
    except Exception as exc:
        raise HTTPException(500, f"任务执行失败: {exc}") from exc
    return _pack(result, thread)


class AgentTaskHistory(BaseModel):
    session_id: str = Field(..., description="任务会话(thread_id)")
    limit: int = Field(30, ge=1, le=100, description="返回历史条数上限")


@router.post("/agent-tasks/history")
async def agent_task_history(req: AgentTaskHistory, user_id: str = Depends(get_current_user_id)) -> dict:
    """Time Travel：列出线程的 checkpoint 历史(新→旧)，每条含 checkpoint_id 可回退/分叉。"""
    items = await list_task_history(req.session_id, req.limit)
    return {"session_id": req.session_id, "history": items}


@router.post("/agent-tasks/confirm")
async def confirm_agent_task(req: AgentTaskConfirm, user_id: str = Depends(get_current_user_id)) -> dict:
    """HITL 恢复：提交决策，从上次 interrupt 处继续执行（同一 thread_id）。"""
    graph = get_task_agent()
    decision = {"verb": req.verb, "action": req.action, "source": req.source}
    try:
        result = await graph.ainvoke(
            Command(resume=decision),
            config={"configurable": {"thread_id": req.session_id}},
        )
    except Exception as exc:
        raise HTTPException(500, f"恢复失败: {exc}") from exc
    return _pack(result, req.session_id)