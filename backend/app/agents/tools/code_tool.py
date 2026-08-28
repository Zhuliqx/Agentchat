"""代码执行 Agent 工具（受限沙箱执行 Python）。"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.middleware import resilience_middleware
from app.agents.prompts import CODE_SYSTEM_PROMPT
from app.config import settings


@lru_cache(maxsize=1)
def build_code_agent():
    """构建代码 Agent（受限执行 Python + LLM）。"""
    from langchain.agents import create_agent

    return create_agent(
        get_llm("light"),
        tools=[_build_execute_python_tool()],
        system_prompt=CODE_SYSTEM_PROMPT,
        middleware=[resilience_middleware()],
    )


class _CodeExecQuery(BaseModel):
    """execute_python_code 工具入参。"""

    code: str = Field(description="要执行的 Python 代码（受限沙箱，仅纯计算标准库）")


def _build_execute_python_tool() -> StructuredTool:
    """受限执行 Python 代码，返回 stdout/错误。"""

    def _run(code: str) -> str:
        from app.agents.code_executor import execute_code

        r = execute_code(
            code,
            timeout=settings.code_exec_timeout,
            max_output=settings.code_exec_max_output,
        )
        lines = []
        if r["stdout"]:
            lines.append("stdout:\n" + r["stdout"])
        if r["stderr"]:
            lines.append("stderr:\n" + r["stderr"])
        if r["error"]:
            lines.append("错误:\n" + r["error"])
        if not lines:
            lines.append("(代码执行完成，无输出)")
        return "\n\n".join(lines)

    return StructuredTool(
        name="execute_python_code",
        description=(
            "在受限沙箱中执行一段 Python 代码并返回运行结果（stdout/错误）。"
            "当需要实际计算、验证算法、生成数据或运行脚本时使用；"
            "环境仅支持纯计算标准库（math/json/datetime/random/collections 等），禁文件/网络/子进程。"
        ),
        args_schema=_CodeExecQuery,
        func=_run,
    )
