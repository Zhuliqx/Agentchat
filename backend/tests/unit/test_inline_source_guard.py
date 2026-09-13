"""原始文件读取白名单：只允许 uploads 与内置知识库目录，且删除只认 uploads。

回归场景：来源 chips 指向 data/kb/*.md 时一直 404（白名单只放开了 uploads），
而删除路径若跟着放开，会把仓库内置知识库连带删掉。
"""
from __future__ import annotations

from pathlib import Path

from app.api.routes.rag import (
    KB_ROOT,
    UPLOAD_ROOT,
    _safe_inline_source,
    _safe_source_in_uploads,
)


def test_uploads_and_kb_are_readable():
    assert _safe_inline_source(UPLOAD_ROOT / "abc" / "note.md")
    assert _safe_inline_source(KB_ROOT / "company.md")


def test_outside_paths_rejected():
    assert not _safe_inline_source(Path("/etc/passwd"))
    assert not _safe_inline_source(UPLOAD_ROOT / ".." / ".." / "app" / "config.py")
    assert not _safe_inline_source(Path("C:/Windows/win.ini"))


def test_only_uploads_can_be_deleted():
    assert _safe_source_in_uploads(UPLOAD_ROOT / "abc" / "note.md")
    # 内置知识库不可删：否则会删掉随应用分发的 data/kb
    assert not _safe_source_in_uploads(KB_ROOT / "company.md")
