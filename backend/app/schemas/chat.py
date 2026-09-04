"""聊天相关的请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """聊天请求体。

    注意：user_id 不属于请求体。长期记忆/知识库的归属一律取
    Authorization 认证出的用户（extra="forbid" 防止旧客户端用
    user_id 指定他人命名空间造成越权）。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = None  # 为空则新建会话（同时作为 Checkpointer 的 thread_id）
    message: str = Field(..., min_length=1, max_length=20000)
    use_rag: bool = True
    use_search: bool = True
    use_memory: bool = True  # 对话时是否启用长期记忆（remember/recall）
    resume: Optional[str] = None  # HITL：非空表示从上次 interrupt 处继续（用户确认值）
    checkpoint_id: Optional[str] = None  # Time Travel：从指定历史 checkpoint 继续（分叉）


class AgentEvent(BaseModel):
    """流式事件，便于前端展示 Agent 运行过程。"""

    type: Literal[
        "start", "agent", "tool", "message", "end", "error", "interrupt",
    ]
    content: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    events: list[AgentEvent] = Field(default_factory=list)
    used_agents: list[str] = Field(default_factory=list)
    hitl_pending: Optional[dict] = None  # HITL：非空表示等待用户人工确认
