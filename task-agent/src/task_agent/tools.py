"""工具调用执行器与内置纯计算工具（零第三方依赖）。"""
from __future__ import annotations

import ast
import inspect
import operator
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from task_agent.executor import ExecuteRequest, StepResult
from task_agent.llm import LLMFactory, llm_text
from task_agent.nodes import _jump_json
from task_agent.prompts import TOOLCALL_PROMPT


@dataclass(frozen=True)
class Tool:
    """工具声明：name / description / 参数说明（JSON Schema 子集，零依赖）。"""

    name: str
    description: str
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    func: Callable[..., Any] | None = None


def _safe_calc(expression: str) -> str:
    """AST 白名单安全求值（禁 eval / 属性访问 / IO）。"""
    _OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.AST):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError("不支持的表达式")

    try:
        return str(_eval(ast.parse(expression, mode="eval")))
    except Exception as exc:
        return f"计算失败：{exc}"


def _current_time(timezone_name: str = "Asia/Shanghai") -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _random_number(lo: float = 1, hi: float = 100) -> str:
    return str(random.randint(int(lo), int(hi)))


builtin_tools: list[Tool] = [
    Tool(
        "calculator",
        "安全计算数学表达式，如 '1 + 2 * 3'",
        {"expression": {"type": "string", "description": "数学表达式"}},
        _safe_calc,
    ),
    Tool(
        "current_time",
        "获取指定时区的当前时间",
        {"timezone_name": {"type": "string", "description": "IANA 时区名，默认 Asia/Shanghai"}},
        _current_time,
    ),
    Tool(
        "random_number",
        "生成指定范围内的随机整数",
        {
            "lo": {"type": "number", "description": "下限，默认 1"},
            "hi": {"type": "number", "description": "上限，默认 100"},
        },
        _random_number,
    ),
]


class ToolCallingExecutor:
    """工具调用执行器：LLM 决定调工具或直答，最多 max_tool_calls 轮。

    作为 task-agent 的 Executor 注入；把 LangChain 的"工具调用循环"概念
    以零依赖方式实现（引擎不感知工具，保持接口缝设计）。
    """

    def __init__(
        self,
        llm_factory: LLMFactory,
        tools: list[Tool] | None = None,
        max_tool_calls: int = 3,
    ) -> None:
        self._llm_factory = llm_factory
        self._tools = {t.name: t for t in (tools or [])}
        self.max_tool_calls = max(1, max_tool_calls)

    def _tool_desc(self) -> str:
        lines = []
        for t in self._tools.values():
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in (t.parameters or {}).items())
            lines.append(f"- {t.name}({params}): {t.description}")
        return "\n".join(lines) or "（无工具）"

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        action = request.action
        trace: list[str] = []
        for _ in range(self.max_tool_calls):
            resp = (
                await llm_text(
                    self._llm_factory(),
                    TOOLCALL_PROMPT.format(tools=self._tool_desc(), action=action),
                )
            ).strip()
            data = _jump_json(resp)
            if not data:
                # LLM 未按 JSON 格式输出 → 视为直接回答（避免误报工具错误）
                return StepResult(answer=resp or "（无输出）")
            if data.get("answer") is not None:
                text = str(data["answer"]).strip()
                if trace:
                    text = f"{text}\n[已调用] {' | '.join(trace)}"
                return StepResult(answer=text or "（无输出）")
            name = str(data.get("tool") or "")
            tool = self._tools.get(name)
            if tool is None or tool.func is None:
                return StepResult(
                    answer=f"工具不存在：{name or '空'}（可用：{', '.join(self._tools)}）"
                )
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            try:
                result = tool.func(**args)
                if inspect.isawaitable(result):
                    result = await result
                trace.append(f"{name}={result}")
                action = f"已调用 {name} 得到结果：{result}。请据此给出最终回答。"
            except Exception as exc:  # noqa: BLE001 - 工具失败返回友好错误
                return StepResult(answer=f"工具 {name} 执行失败：{exc}")
        return StepResult(answer="达到工具调用上限，请基于已有信息回答。")
