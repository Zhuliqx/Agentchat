"""批量摄入：纯图片文档保留（图文双通道）与 Postgres 分批提交。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.rag import batch_ingest


def _plan(source: str, n_chunks: int, images: list | None = None) -> batch_ingest._FilePlan:
    return batch_ingest._FilePlan(
        source=source,
        filename=Path(source).name,
        chunks=[{"text": f"t{i}", "metadata": {"chunk": i}} for i in range(n_chunks)],
        content_hash="h",
        images=images or [],
    )


class _Query:
    def __init__(self, log: list[tuple[str, int]]) -> None:
        self._log = log

    def filter(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    def all(self):
        return []

    def delete(self, **kwargs):
        self._log.append(("delete", 0))
        return 0

    def update(self, *args, **kwargs):
        self._log.append(("update", 0))
        return 0


class _Session:
    def __init__(self, log: list[tuple[str, int]], batches: list[list[str]]) -> None:
        self._log = log
        self._batches = batches

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, *args, **kwargs):
        return _Query(self._log)

    def add_all(self, rows):
        self._log.append(("add", len(rows)))
        self._batches.append([r.source for r in rows])

    def commit(self):
        self._log.append(("commit", 0))


def _install_fakes(
    monkeypatch, plans: list, log: list[tuple[str, int]], *, dual: bool
) -> list[list[str]]:
    batches: list[list[str]] = []
    monkeypatch.setattr(batch_ingest, "_plan_file", lambda p: plans.pop(0))
    monkeypatch.setattr(batch_ingest, "SessionLocal", lambda: _Session(log, batches))
    monkeypatch.setattr(
        "app.rag.embedding.embed_texts_cached", lambda texts: [[0.0]] * len(texts)
    )
    monkeypatch.setattr(batch_ingest.vector_store, "delete_by_sources", lambda *a, **k: None)
    monkeypatch.setattr(batch_ingest.vector_store, "add_chunks_multi", lambda *a, **k: ["ok"])
    monkeypatch.setattr(batch_ingest, "invalidate_docs_signature", lambda *a, **k: None)
    monkeypatch.setattr(batch_ingest.settings, "embed_with_context", False)
    monkeypatch.setattr(batch_ingest.settings, "embed_batch_size", 5000)
    monkeypatch.setattr(batch_ingest.settings, "image_dual_channel", dual)
    return batches


def test_plan_file_keeps_image_only_doc_in_dual_channel(monkeypatch, tmp_path):
    f = tmp_path / "form.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        batch_ingest, "load_document", lambda p: {"text": "", "tables": [], "images": [object()]}
    )
    monkeypatch.setattr(batch_ingest.settings, "image_dual_channel", True)
    monkeypatch.setattr(batch_ingest.settings, "image_ocr_enabled", False)
    monkeypatch.setattr(batch_ingest.settings, "image_vlm_enabled", False)
    plan = batch_ingest._plan_file(f)
    assert plan is not None
    assert plan.chunks == []
    assert len(plan.images) == 1


def test_plan_file_skips_image_only_doc_without_dual_channel(monkeypatch, tmp_path):
    f = tmp_path / "form.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        batch_ingest, "load_document", lambda p: {"text": "", "tables": [], "images": [object()]}
    )
    monkeypatch.setattr(batch_ingest.settings, "image_dual_channel", False)
    monkeypatch.setattr(batch_ingest.settings, "image_ocr_enabled", False)
    monkeypatch.setattr(batch_ingest.settings, "image_vlm_enabled", False)
    assert batch_ingest._plan_file(f) is None


def test_ingest_paths_batch_commits_in_pg_batches(monkeypatch):
    log: list[tuple[str, int]] = []
    plans = [_plan(f"/s/{i}.txt", 4000) for i in range(7)]  # 28000 块
    batches = _install_fakes(monkeypatch, plans, log, dual=False)
    stats = batch_ingest.ingest_paths_batch(
        [Path(f"/p{i}.txt") for i in range(7)], user_id="u"
    )
    assert stats == {"files": 7, "chunks": 28000, "failed": []}
    # 批次按 source 对齐：每篇 4000 块 → [8000, 8000, 8000, 4000]
    assert [n for name, n in log if name == "add"] == [8_000, 8_000, 8_000, 4_000]
    # 同一 source 的行不跨批（中断时不会留下"半篇"）
    seen: set[str] = set()
    for batch_sources in batches:
        counts = Counter(batch_sources)
        assert all(n == 4000 for n in counts.values()), counts
        assert not (set(counts) & seen), set(counts) & seen
        seen |= set(counts)
    assert len(seen) == 7
    # 删除与首批插入同事务提交，避免"删了没插"的窗口
    assert [name for name, _ in log][:3] == ["delete", "add", "commit"]


def test_ingest_paths_batch_writes_image_vectors_for_image_only_plan(monkeypatch):
    log: list[tuple[str, int]] = []
    plans = [_plan("/s/form.pdf", 0, images=[object()])]
    _install_fakes(monkeypatch, plans, log, dual=True)
    calls: list[str] = []
    monkeypatch.setattr(
        batch_ingest, "_write_image_vectors", lambda images, source, user_id: calls.append(source)
    )
    stats = batch_ingest.ingest_paths_batch([Path("/p/form.pdf")], user_id="u")
    assert calls == ["/s/form.pdf"]
    assert stats["files"] == 1
    assert stats["chunks"] == 0


def test_ingest_paths_batch_accepts_generator_input(monkeypatch):
    """生成器输入不能被进度回调消费：3 篇都要处理（回归 Codex 评审 P2）。"""
    log: list[tuple[str, int]] = []
    plans = [_plan(f"/s/{i}.txt", 10) for i in range(3)]
    batches = _install_fakes(monkeypatch, plans, log, dual=False)
    stats = batch_ingest.ingest_paths_batch(
        (Path(f"/p{i}.txt") for i in range(3)), user_id="u"
    )
    assert stats == {"files": 3, "chunks": 30, "failed": []}
    assert {s for batch in batches for s in batch} == {f"/s/{i}.txt" for i in range(3)}
