"""MCP 客户端管理器。

统一管理两类 MCP 连接：
1. 自建 MCP 服务器（stdio 方式，见 app/mcp_integration/servers/）
2. 外部 MCP 服务器（HTTP / streamable http，在 .env 的 EXTERNAL_MCP_SERVERS 配置）

对外暴露：
- get_langchain_tools() 转换为 LangChain 工具供 Agent 调用
- start_all() / stop_all()  生命周期管理
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import create_model

from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # mcp 2.x 中函数名为 streamable_http_client；兼容旧版 streamablehttp_client
    try:
        from mcp.client.streamable_http import streamable_http_client as make_http_client
    except ImportError:  # pragma: no cover
        from mcp.client.streamable_http import streamablehttp_client as make_http_client  # type: ignore
    from mcp.types import TextContent, Tool as McpTool
except ImportError:  # pragma: no cover
    logger.warning("mcp SDK 未安装，MCP 功能将不可用")


@dataclass
class McpServerHandle:
    """单个 MCP 服务器连接句柄。"""

    name: str
    transport: str  # stdio | http
    session: "ClientSession"
    tools: list["McpTool"] = field(default_factory=list)


class McpClientManager:
    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._handles: dict[str, McpServerHandle] = {}

    # ---------------- 生命周期 ----------------

    async def start_all(self) -> list[str]:
        """启动全部已配置的 MCP 服务器，返回已连接服务器名。"""
        for name, (cmd, args) in self._builtin_servers().items():
            try:
                await self._start_stdio(name, cmd, args)
            except Exception as exc:
                logger.warning("自建 MCP '%s' 启动失败: %s", name, exc)
        for name, url in settings.external_mcp_dict.items():
            try:
                await self._start_http(name, url)
            except Exception as exc:
                logger.warning("外部 MCP '%s' 连接失败: %s", name, exc)
        return list(self._handles.keys())

    async def stop_all(self) -> None:
        await self._stack.aclose()
        self._handles.clear()

    # ---------------- 连接建立 ----------------

    def _builtin_servers(self) -> dict[str, tuple[str, list[str]]]:
        return {
            "db": (settings.mcp_db_server_cmd, [settings.mcp_db_server_args]),
            "time": (settings.mcp_time_server_cmd, [settings.mcp_time_server_args]),
        }

    async def _start_stdio(self, name: str, cmd: str, args: list[str]) -> None:
        params = StdioServerParameters(command=cmd, args=args, cwd=str(BASE_DIR))
        ctx = stdio_client(params)
        read, write = await self._stack.enter_async_context(ctx)
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        self._handles[name] = McpServerHandle(name=name, transport="stdio", session=session, tools=tools)
        logger.info("MCP stdio 服务器 '%s' 已连接，工具数: %d", name, len(tools))

    async def _start_http(self, name: str, url: str) -> None:
        # streamable_http_client 返回 3 元组：read / write / get_session_id
        ctx = make_http_client(url)
        read, write, _get_session_id = await self._stack.enter_async_context(ctx)
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        self._handles[name] = McpServerHandle(name=name, transport="http", session=session, tools=tools)
        logger.info("外部 MCP 服务器 '%s' 已连接，工具数: %d", name, len(tools))

    # ---------------- 查询 ----------------

    def server_names(self) -> list[str]:
        """返回当前已连接服务器的名称列表。"""
        return list(self._handles.keys())

    def get_langchain_tools(self) -> list[BaseTool]:
        """把当前已连接的所有 MCP 工具转换为 LangChain 工具。"""
        tools: list[BaseTool] = []
        for server_name, handle in self._handles.items():
            for mcp_tool in handle.tools:
                tools.append(
                    self._to_langchain_tool(
                        server_name, mcp_tool, handle.session
                    )
                )
        return tools

    # ---------------- 转换 ----------------

    @staticmethod
    def _to_langchain_tool(server_name: str, mcp_tool: "McpTool", session: "ClientSession") -> StructuredTool:
        """将 MCP 工具包装为 LangChain StructuredTool。

        工具名加服务器前缀避免冲突：{server}_{tool}
        """
        name = f"{server_name}_{mcp_tool.name}"
        description = mcp_tool.description or f"MCP 工具 {mcp_tool.name}（来自 {server_name}）"
        args_schema = json_schema_to_pydantic(mcp_tool.inputSchema, name=f"{name}Args")

        async def _arun(**kwargs: Any) -> str:
            result = await session.call_tool(mcp_tool.name, arguments=kwargs)
            parts = []
            for content in result.content:
                if isinstance(content, TextContent):
                    parts.append(content.text)
                else:
                    parts.append(str(content))
            return "\n".join(parts)

        return StructuredTool(
            name=name,
            description=description,
            args_schema=args_schema,
            coroutine=_arun,
        )


def json_schema_to_pydantic(schema: dict[str, Any], name: str = "ToolArgs") -> Any:
    """把 JSON Schema 的 properties 转换为 Pydantic 模型（用于 LangChain 工具参数校验）。"""
    fields: dict[str, Any] = {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    type_map = {
        "string": (str, ...),
        "integer": (int, ...),
        "number": (float, ...),
        "boolean": (bool, ...),
        "array": (list, ...),
        "object": (dict, ...),
    }

    for prop_name, prop in properties.items():
        t = prop.get("type", "string")
        annotation, _default = type_map.get(t, (str, ...))
        if prop_name not in required:
            annotation = annotation | None  # type: ignore[operator]
        fields[prop_name] = (annotation, ... if prop_name in required else None)

    if not fields:
        fields["_unused"] = (str, None)

    return create_model(name, **fields)


# 全局单例（FastAPI lifespan 中管理启停）
_manager: McpClientManager | None = None


def get_mcp_manager() -> McpClientManager:
    global _manager
    if _manager is None:
        _manager = McpClientManager()
    return _manager
