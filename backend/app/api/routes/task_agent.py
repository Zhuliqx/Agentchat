"""自主任务 Agent 的 API：接收模糊目标，返回 plan + findings + final_answer。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id
from app.task_agent.graph import get_task_agent

router = APIRouter()


class AgentTaskRun(BaseModel):
    goal: str = Field(..., min_length=1, max_length=2000, description="用户目标")
    session_id: str | None = None  # 为空则新建任务会话（checkpoint thread_id）


@router.post("/agent-tasks/run")
async def run_agent_task(req: AgentTaskRun, user_id: str = Depends(get_current_user_id)) -> dict:
    graph = get_task_agent()
    thread = req.session_id or f"task-{uuid.uuid4().hex[:12]}"
    try:
        result = await graph.ainvoke(
            {"goal": req.goal},
            config={"configurable": {"thread_id": thread}},
        )
    except Exception as exc:
        raise HTTPException(500, f"任务执行失败: {exc}") from exc
    return {
        "session_id": thread,
        "plan": result["plan"],
        "findings": result["findings"],
        "final_answer": result["final_answer"],
    }