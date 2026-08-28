"""流式输出辅助（开场白缓冲/去重）。"""
from __future__ import annotations


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
