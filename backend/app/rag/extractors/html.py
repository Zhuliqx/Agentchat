"""HTML → 纯文本（stdlib HTMLParser，避免引入 bs4）。"""
from __future__ import annotations

from html.parser import HTMLParser


class _HtmlTextExtractor(HTMLParser):
    """用 stdlib html.parser 提取 HTML 可见文本。

    - 丢弃 <script>/<style> 内容；
    - 内联标签（b/i/span/a 等）文本拼接，不随意换行；
    - 块级/换行标签（p/div/li/h1-6/table/tr/br）处换行；
    - 表格单元格用 " | " 分隔（与 docx 表一致），行末换行；
    - 列表项加 "- " 前缀。
    """

    _SKIP_TAGS = {"script", "style"}
    _BLOCK_TAGS = {
        "p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "ul", "ol", "dl", "li",
    }
    _CELL_TAGS = {"td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []  # 普通文本行
        self._buf: list[str] = []    # 当前普通行缓冲
        self._row_parts: list[str] = []  # 当前表格行的单元格
        self._cell_buf: list[str] = []   # 当前单元格缓冲
        self._skip_depth = 0
        self._in_cell = False

    def _flush(self) -> None:
        line = "".join(self._buf).strip()
        self._buf = []
        if line:
            self._lines.append(line)

    def _flush_row(self) -> None:
        parts = [p.strip() for p in self._row_parts if p.strip()]
        if parts:
            self._lines.append(" | ".join(parts))
        self._row_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self._flush()
        elif tag == "li":
            self._flush()
            self._buf.append("- ")
        elif tag in self._CELL_TAGS:
            self._in_cell = True
            self._cell_buf = []
        elif tag == "tr":
            self._flush_row()
        elif tag in self._BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self._CELL_TAGS and self._in_cell:
            self._in_cell = False
            self._row_parts.append("".join(self._cell_buf))
            self._cell_buf = []
        elif tag == "tr":
            self._flush_row()
        elif tag == "li":
            self._flush()
        elif tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_cell:
            self._cell_buf.append(data)
        else:
            self._buf.append(data)

    def text(self) -> str:
        self._flush()
        self._flush_row()
        return "\n".join(self._lines)


def _html_to_text(raw: str) -> str:
    """HTML → 纯文本（剥离标签与脚本/样式）；解析失败时回退原始文本。"""
    parser = _HtmlTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return raw
    return parser.text() or raw
