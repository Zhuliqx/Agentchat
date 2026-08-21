"""定时/批处理任务管理接口（配合后台调度器）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db import postgres
from app.scheduler import TASK_REGISTRY, compute_next_run

_SCHEDULE_ERROR = "调度表达式非法（支持 interval:<秒> 或 cron:<分钟>）"

router = APIRouter()


class TaskOut(BaseModel):
    id: str
    name: str
    task_type: str
    task_label: str
    task_desc: str
    schedule: str
    enabled: bool
    created_at: str
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    next_run_at: str | None


class TaskIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    task_type: str = Field(..., min_length=1, max_length=64)
    schedule: str = Field(default="interval:3600", max_length=64)

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, v: str) -> str:
        if compute_next_run(v) is None:
            raise ValueError(_SCHEDULE_ERROR)
        return v


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    schedule: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, v: str | None) -> str | None:
        if v is not None and compute_next_run(v) is None:
            raise ValueError(_SCHEDULE_ERROR)
        return v


def _out(t) -> TaskOut:
    entry = TASK_REGISTRY.get(t.task_type, {})
    return TaskOut(
        id=t.id,
        name=t.name,
        task_type=t.task_type,
        task_label=entry.get("label", t.task_type),
        task_desc=entry.get("desc", ""),
        schedule=t.schedule,
        enabled=t.enabled,
        created_at=t.created_at.isoformat(),
        last_run_at=t.last_run_at.isoformat() if t.last_run_at else None,
        last_status=t.last_status,
        last_error=t.last_error,
        next_run_at=t.next_run_at.isoformat() if t.next_run_at else None,
    )


@router.get("/registry")
def registry() -> list[dict]:
    """可用任务类型及说明（供前端下拉选择）。"""
    return [
        {"type": k, "label": v["label"], "desc": v["desc"]} for k, v in TASK_REGISTRY.items()
    ]


@router.get("", response_model=list[TaskOut])
def list_tasks():
    return [_out(t) for t in postgres.list_tasks()]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskIn):
    if body.task_type not in TASK_REGISTRY:
        raise HTTPException(400, f"未知任务类型: {body.task_type}")
    t = postgres.create_task(body.name, body.task_type, body.schedule)
    postgres.mark_task_result(t.id, "", None, compute_next_run(body.schedule))
    return _out(t)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: str, body: TaskPatch):
    t = postgres.update_task(task_id, name=body.name, schedule=body.schedule, enabled=body.enabled)
    if not t:
        raise HTTPException(404, "任务不存在")
    if body.schedule is not None:
        postgres.mark_task_result(t.id, t.last_status or "", t.last_error,
                                  compute_next_run(body.schedule))
    return _out(t)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str):
    if not postgres.delete_task(task_id):
        raise HTTPException(404, "任务不存在")


@router.post("/{task_id}/run", response_model=TaskOut)
async def run_now(task_id: str):
    """立即执行一次（手动触发，不改变调度）。"""
    t = postgres.get_task(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    from app.scheduler import _run_task

    postgres.mark_task_result(t.id, "running", None, t.next_run_at)
    await _run_task(t.id)
    return _out(postgres.get_task(task_id))
