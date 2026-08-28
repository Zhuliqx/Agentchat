"""LangGraph 运行时上下文定义。

运行时上下文（Runtime Context）仅对当次调用有效，不会被持久化，
也不会在同一会话的下一次调用中自动恢复。用于传递当前登录用户、
请求来源等"当次调用相关"的信息。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserContext:
    """当前用户上下文（通过 LangGraph 的 context_schema + context 传递）。

    - user_id：当前登录用户（知识库 / 长期记忆按此隔离）；
    - session_id：当前会话 id（Checkpointer thread_id）。供检索工具拼装
      多轮对话上下文（RAG_MULTI_TURN_CONTEXT）——注意它**不参与图状态**，
      仅当次调用传递；无会话时为空串。
    """

    user_id: str = "default"
    session_id: str = ""
