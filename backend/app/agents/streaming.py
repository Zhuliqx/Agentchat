"""流式输出辅助：开场白缓冲/去重（_PreludeDedupe）与 Supervisor 输出装配器。

``SupervisorStreamer`` 把 stream_agent 里纯输出装配的状态机（开场白缓冲、
工具事件去重、工具后重复前缀剔除、最终拼接）从 graph.py 抽出来，图编排只负责
迭代 LangGraph 流并把 chunk/update 喂给装配器。
"""
from __future__ import annotations

from typing import Awaitable, Callable


class SupervisorStreamer:
    """Supervisor 输出装配器（纯逻辑，不触碰 LangGraph 流本身）。

    语义（与抽离前逐行一致）：
    - 工具触发前不逐字显示（prelude_buf）；超过 PRELUDE_FLUSH 判定为直接回答，
      开始平滑逐字流式；
    - prelude_total 保留全部开场白，供工具后去重（LLM 常连同答案重新生成）；
    - 仅对本次实际注册的工具发 tool 事件（防幻觉调用未注册工具的 phantom 事件）。
    """

    PRELUDE_FLUSH = 40

    def __init__(
        self,
        on_token: Callable[[str], Awaitable[None]] | None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None,
        user_id: str = "default",
    ) -> None:
        self._on_token = on_token
        self._on_tool_event = on_tool_event
        self.user_id = user_id or "default"
        self.answer_parts: list[str] = []
        # 本次实际注册的工具名集合（按开关）：过滤模型幻觉调用的未注册工具
        self.registered_tools: set[str] = {"mcp_agent"}
        self.saw_tool_call = False
        self._prelude_total: list[str] = []  # 全部开场白（含已流式部分）
        self._prelude_buf: list[str] = []    # 尚未判定是否直接回答的缓冲文本
        self._streaming_direct = False       # 已判定为直接回答 → 逐字流式
        self._dedupe: _PreludeDedupe | None = None
        self._pending_tool_name: str | None = None

    def register_tool(self, name: str) -> None:
        """登记本次实际注册的工具（未登记的 tool_call 不发事件）。"""
        self.registered_tools.add(name)

    async def _push(self, text: str) -> None:
        """推送一段文本到答案流（并记录到 answer_parts）。"""
        self.answer_parts.append(text)
        if self._on_token:
            await self._on_token(text)

    async def emit_tool(self, name: str) -> None:
        """统一处理工具调用：丢弃未流式的开场白碎片并发出 tool 事件。"""
        is_real = name in self.registered_tools
        if is_real and not self.saw_tool_call:
            if not self._streaming_direct:
                self._prelude_buf.clear()  # 丢弃未显示的开场白碎片
            self._dedupe = _PreludeDedupe("".join(self._prelude_total))
            self.saw_tool_call = True
        if self._on_tool_event is not None and is_real and name != self._pending_tool_name:
            self._pending_tool_name = name
            data: dict = {}
            # 引用溯源：rag_agent 执行后附带检索命中的文档来源
            if name == "rag_agent":
                from app.agents.tools.sources import get_recent_rag_sources

                data["sources"] = get_recent_rag_sources(self.user_id)
            await self._on_tool_event(
                {"type": "tool", "content": f"工具: {name}", "data": data}
            )

    async def record_tool_prelude(self, text: str) -> None:
        """工具即将执行时的开场白 chunk：记入 prelude_total，直接回答则已流式。"""
        self._prelude_total.append(text)
        if self._streaming_direct:
            await self._push(text)
        else:
            self._prelude_buf.append(text)

    async def feed(self, text: str) -> None:
        """工具调用前（或直接回答）的文本：未判定时缓冲，超阈值开始逐字流式。"""
        self._prelude_total.append(text)
        if self._streaming_direct:
            await self._push(text)
        else:
            self._prelude_buf.append(text)
            if len("".join(self._prelude_buf)) >= self.PRELUDE_FLUSH:
                self._streaming_direct = True
                await self._push("".join(self._prelude_buf))
                self._prelude_buf.clear()

    async def feed_answer(self, text: str) -> None:
        """工具后的最终答案：流式前缀匹配跳过重复的开场白前缀。"""
        if self._dedupe is not None and self._dedupe.active:
            text = self._dedupe.feed(text)
        if text:
            await self._push(text)

    async def flush(self) -> None:
        """流结束：补推尚未判定的短文本（<阈值，如很短的直接回答）。"""
        if self._prelude_buf:
            await self._push("".join(self._prelude_buf))
            self._prelude_buf.clear()

    def answer(self) -> str:
        return "".join(self.answer_parts)


class _PreludeDedupe:
    """流式去重：跳过与"已推送开场白"匹配的前缀。

    工具调用后，LLM 常把开场白连同最终答案一起重新生成（完整重复一遍）。
    由于流式输出按小分块到达（首个分块往往只是开场白的一小段前缀），
    不能直接 `text.startswith(整段开场白)` 判断（首个分块永远不等于整段开场白，
    导致去重失败、完整重复）。这里按字符逐块前缀匹配：

    - 分块完全属于开场白前缀 → 丢弃（开场白已推送过）；
    - 一旦出现分歧 → 只推送分歧后的部分，之后不再去重；
    - 完全没重复 → 首个字符即分歧，原样推送（无额外延迟）。
    """

    __slots__ = ("expected", "matched")

    def __init__(self, expected: str):
        self.expected = expected or ""
        self.matched = 0

    @property
    def active(self) -> bool:
        return self.matched < len(self.expected)

    def feed(self, text: str) -> str:
        """输入一个 token 分块，返回应推送的文本（可能为空串）。"""
        if self.matched >= len(self.expected):
            return text
        i, j = self.matched, 0
        n = len(text)
        while j < n and i < len(self.expected) and text[j] == self.expected[i]:
            j += 1
            i += 1
        self.matched = i
        if j < n:
            self.matched = len(self.expected)  # 出现分歧：之后直接推送
            return text[j:]
        return ""
