"""第三批健壮性单元测试：上传流式落盘限流等。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


class _FakeUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._it = iter(chunks)
        self.file = type("F", (), {"read": self._read})()

    def _read(self, size: int = -1) -> bytes:
        return next(self._it, b"")


def _target() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / ".upload_stream_probe.bin"
    )


def test_upload_stream_writes_all_chunks():
    from app.api.routes.rag import _save_upload_stream

    target = _target()
    try:
        _save_upload_stream(
            _FakeUpload([b"ab", b"cd", b"ef"]), target, max_bytes=10
        )
        assert target.read_bytes() == b"abcdef"
    finally:
        target.unlink(missing_ok=True)


def test_upload_stream_aborts_when_over_limit():
    from app.api.routes.rag import _save_upload_stream

    target = _target()
    try:
        with pytest.raises(HTTPException) as exc:
            _save_upload_stream(
                _FakeUpload([b"a" * 1024, b"b" * 1024, b"c" * 1024]),
                target,
                max_bytes=2048,
            )
        assert exc.value.status_code == 413
        assert not target.exists(), "超限后不应留下半截文件"
    finally:
        target.unlink(missing_ok=True)
