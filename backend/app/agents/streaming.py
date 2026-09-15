"""流式输出辅助：开场白缓冲/去重（_PreludeDedupe）与 Supervisor 输出装配器。

``SupervisorStreamer`` 把 stream_agent 里纯输出装配的状态机（开场白缓冲、
工具事件去重、工具后重复前缀剔除、最终拼接）从 graph.py 抽出来，图编排只负责
迭代 LangGraph 流并把 chunk/update 喂给装配器。
"""
from __future__ import annotations

import math
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

    @staticmethod
    def _prelude_weight(text: str) -> int:
        """按"信息量"折算开场白长度：中文 1 字算 1，英文约 4 字符算 1。

        直接按字符数比较会误判英文开场白：一句
        "I'll search for the latest AI industry news for you."（51 字符）会被
        当成"够长的正式回答"提前流式输出，随后工具执行完再拼上中文答案，
        用户就看到中英混排。折算后中英同一把尺子。
        """
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        ascii_alpha = sum(1 for ch in text if ch.isascii() and ch.isalpha())
        rest = len(text) - cjk - ascii_alpha
        return cjk + math.ceil(ascii_alpha / 4) + math.ceil(rest / 2)

    def __init__(
        self,
        on_token: Callable[[str], Awaitable[None]] | None,
        on_tool_event: Callable[[dict], Awaitable[None]] | None,
        run_id: str = "",
    ) -> None:
        self._on_token = on_token
        self._on_tool_event = on_tool_event
        self.run_id = run_id or ""
        self.answer_parts: list[str] = []
        # 本次实际注册的工具名集合（按开关）：过滤模型幻觉调用的未注册工具
        self.registered_tools: set[str] = {"mcp_agent"}
        self.saw_tool_call = False
        self._prelude_total: list[str] = []  # 全部开场白（含已流式部分）
        self._prelude_buf: list[str] = []    # 尚未判定是否直接回答的缓冲文本
        self._streaming_direct = False       # 已判定为直接回答 → 逐字流式
        self._dedupe: _PreludeDedupe | None = None
        self._pending_tool_name: str | None = None
        self._emitted_sources: dict[str, list[dict]] = {}
        # 去重器延后到答案开始再建：工具执行期间到达的开场白也要计入 expected
        self._dedupe_pending = False

    def register_tool(self, name: str) -> None:
        """登记本次实际注册的工具（未登记的 tool_call 不发事件）。"""
        self.registered_tools.add(name)

    async def _push(self, text: str) -> None:
        """推送一段文本到答案流（并记录到 answer_parts）。"""
        self.answer_parts.append(text)
        if self._on_token:
            await self._on_token(text)

    async def emit_tool(self, name: str) -> None:
        """统一处理工具调用：丢弃未流式的开场白碎片并发出 tool 事件。

        rag_agent 的来源要等工具执行完才有：模型刚发起调用时事件里 sources 为空，
        执行后的第二次调用会带来源再补一次（前端按轨道标签去重，只会更新来源、
        不会多出节点）。
        """
        is_real = name in self.registered_tools
        if is_real and not self.saw_tool_call:
            if not self._streaming_direct:
                self._prelude_buf.clear()  # 丢弃未显示的开场白碎片
            # 这里只标记"该去重了"：真正构建放在 feed_answer，
            # 这样工具执行期间补记的开场白（record_tool_prelude）也算进 expected
            self._dedupe_pending = True
            self.saw_tool_call = True
        sources: list[dict] = []
        if is_real and name == "rag_agent":
            from app.agents.tools.sources import get_recent_rag_source_refs

            # [{"path": ..., "hits": 片段数}]，供前端展示"来源 + 命中片段数"
            sources = get_recent_rag_source_refs(self.run_id)
        # 同一工具只发一次事件；但 rag_agent 的来源在执行后才出现，需要补发一次
        has_new_sources = bool(sources) and sources != self._emitted_sources.get(name)
        if (
            self._on_tool_event is not None
            and is_real
            and (name != self._pending_tool_name or has_new_sources)
        ):
            self._pending_tool_name = name
            if sources:
                self._emitted_sources[name] = list(sources)
            data: dict = {}
            if sources:
                data["sources"] = sources
            await self._on_tool_event(
                {"type": "tool", "content": f"工具: {name}", "data": data}
            )

    async def record_tool_prelude(self, text: str) -> None:
        """工具即将执行时的开场白 chunk：只记入 prelude_total 供答案去重。

        不再放进 _prelude_buf——那份缓冲会在流结束时被 flush 补推，
        把开场白追加到答案末尾（工具调用后模型重写完整回答时尤其明显）。
        """
        self._prelude_total.append(text)
        if self._streaming_direct:
            await self._push(text)

    async def feed(self, text: str) -> None:
        """工具调用前（或直接回答）的文本：未判定时缓冲，超阈值开始逐字流式。"""
        self._prelude_total.append(text)
        if self._streaming_direct:
            await self._push(text)
        else:
            self._prelude_buf.append(text)
            if self._prelude_weight("".join(self._prelude_buf)) >= self.PRELUDE_FLUSH:
                self._streaming_direct = True
                await self._push("".join(self._prelude_buf))
                self._prelude_buf.clear()

    async def feed_answer(self, text: str) -> None:
        """工具后的最终答案：流式前缀匹配跳过重复的开场白前缀。"""
        if self._dedupe_pending:
            self._dedupe_pending = False
            self._dedupe = _PreludeDedupe("".join(self._prelude_total))
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
