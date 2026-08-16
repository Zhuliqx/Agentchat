"""自建 MCP 服务器：时间 / 计算等通用工具。

运行方式:
    python backend/scripts/time_server.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time-tools")


@mcp.tool()
def get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间。

    参数 timezone_name: IANA 时区名，如 Asia/Shanghai、UTC。
    """
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(timezone_name))
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@mcp.tool()
def calculate(expression: str) -> str:
    """安全计算一个数学表达式，如 '1 + 2 * 3'（AST 白名单求值，非 eval）。"""
    try:
        import ast
        import operator

        _ops = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
            ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
        }

        def _eval_node(node):
            if isinstance(node, ast.Expression):
                return _eval_node(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in _ops:
                return _ops[type(node.op)](_eval_node(node.left), _eval_node(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in _ops:
                return _ops[type(node.op)](_eval_node(node.operand))
            raise ValueError("不支持的表达式")

        tree = ast.parse(expression, mode="eval")
        return str(_eval_node(tree.body))
    except Exception as exc:
        return f"计算失败: {exc}"
