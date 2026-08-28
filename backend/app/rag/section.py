"""块章节上下文前缀（嵌入侧与 rerank 侧共用）。"""

from __future__ import annotations

from pathlib import Path

# MarkdownHeaderTextSplitter 写入块 metadata 的标题键（见 app/rag/chunkers.split_text）
_HEADER_KEYS = ("H1", "H2", "H3")


def section_prefix(metadata: dict, source: str = "") -> str:
    """从块 metadata 提取章节/文件名前缀。

    Markdown 块带 H1/H2/H3；无标题时回退到文件名。供嵌入侧
    （EMBED_WITH_CONTEXT）与 rerank 侧（RERANK_SECTION_CONTEXT）共用；
    两者都为空返回空串。
    """
    headers = [
        str(metadata[k]) for k in _HEADER_KEYS if str(metadata.get(k) or "").strip()
    ]
    if headers:
        return "[章节] " + " > ".join(headers)
    if source:
        # 统一分隔符：Windows 路径在 Linux 上跑（如 CI）也能正确取 basename
        return "[文档] " + Path(source.replace("\\", "/")).name
    return ""
