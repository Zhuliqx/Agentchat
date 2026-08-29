"""增量摄入一致性集成测试"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpers import (
    db_available,
    milvus_source_texts,
    wait_milvus_converged,
)

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 两段内容：章节 1 不变、章节 2 内容变化 → 增量摄入只改 chunk1
# （内容 < chunk_size=800 时 RecursiveCharacterTextSplitter 会合并为单块，
#   但增量语义不变：v2 的块 hash 与 v1 不同 → 整块替换）
V1 = (
    "第一章 公司介绍\n\n"
    "量子计算公司成立于2020年，总部位于上海。\n\n"
    "第二章 产品\n\n"
    "旗舰产品是量子加速器。联系电话 021-10000000。"
)
V2 = (
    "第一章 公司介绍\n\n"
    "量子计算公司成立于2020年，总部位于上海。\n\n"
    "第二章 产品\n\n"
    "旗舰产品是量子模拟器。联系电话 021-20000000。"
)

pytestmark = pytest.mark.skipif(
    not db_available(), reason="Postgres/Milvus 不可用（需 Docker 依赖）"
)


def test_incremental_reupload_consistency(tmp_path: Path) -> None:
    from app.db.models import Document
    from app.db.postgres import SessionLocal
    from app.rag import vector_store
    from app.rag.ingestion import ingest_file

    doc = tmp_path / "kb_incremental.txt"
    source = str(doc.resolve())
    try:
        # ---- v1 摄入 ----
        doc.write_text(V1, encoding="utf-8")
        r1 = ingest_file(doc, user_id="default")
        assert r1.get("chunks", 0) > 0, f"v1 摄入失败: {r1}"
        with SessionLocal() as db:
            v1_rows = [
                (d.id, d.chunk_index)
                for d in db.query(Document).filter(Document.source == source).all()
            ]
        assert v1_rows, "v1 后 Postgres 应有文档行"
        wait_milvus_converged(source, v1_rows)  # 等 v1 向量可见

        # ---- 同 source 增量重传（内容变化 → 整块替换）----
        doc.write_text(V2, encoding="utf-8")
        r2 = ingest_file(doc, user_id="default")
        assert not r2.get("unchanged"), f"v2 应产生增量写入: {r2}"
        assert r2.get("chunks", 0) > 0, f"v2 摄入失败: {r2}"
        with SessionLocal() as db:
            v2_rows = [
                (d.id, d.chunk_index)
                for d in db.query(Document).filter(Document.source == source).all()
            ]
        assert v2_rows and v2_rows != v1_rows, "v2 后 Postgres 行应更新为新的 doc_id"

        # 等 Milvus 收敛后，(doc_id, chunk_index) 必须与 Postgres 完全一致
        mv = wait_milvus_converged(source, v2_rows)
        assert sorted(mv) == sorted(v2_rows), (
            f"Milvus/Postgres 不一致（幽灵向量残留或融合键错位）\n"
            f"PG: {v2_rows}\nMV: {mv}"
        )

        # v1 特有内容不得残留在 Milvus（精确 token 检查，不受嵌入相似度干扰）
        texts = milvus_source_texts(source)
        assert texts, "Milvus 中应有该 source 的文本"
        assert not any("加速器" in t or "10000000" in t for t in texts), (
            f"v1 旧块仍残留在 Milvus（幽灵向量）: {texts}"
        )
        # 更新后的内容应在 Milvus 中
        assert any("模拟器" in t and "20000000" in t for t in texts), (
            f"更新后的内容未写入 Milvus: {texts}"
        )

        # 检索确认：向量通道能命中更新后的内容
        hits2 = vector_store.search(
            "量子模拟器 021-20000000", top_k=10, score_threshold=0.0
        )
        assert any(h.get("source") == source for h in hits2), "更新后的内容未被检索到"
    finally:
        # 清理，避免污染其他检索回归用例
        vector_store.delete_by_source(source, user_id="default")
        wait_milvus_converged(source, [])  # 等删除收敛，防止幽灵向量累积挤占 top-K
        with SessionLocal() as db:
            db.query(Document).filter(Document.source == source).delete(
                synchronize_session=False
            )
            db.commit()


# ---- 多块部分更新：未变块保留 doc_id，变更块写原始 chunk_index ----
# 每节 > chunk_size 独立成块，A/C 块 v1/v2 逐字节相同，仅 B 增量写入
# 且原始 chunk_index > 0（抓住"写批内位置"的回归）。
_PARTIAL_A = "第一章 公司概况\n" + " ".join(
    f"量子计算公司第{i}项业务是量子芯片设计与低温控制，覆盖研发、生产与测试全流程。" for i in range(1, 40)
)
_PARTIAL_B1 = "第二章 组织架构\n" + " ".join(
    f"组织架构第{i}条：财务部负责预算编制，人事部负责招聘培训，市场部负责品牌推广。" for i in range(1, 40)
)
_PARTIAL_B2 = "第二章 组织架构\n" + " ".join(
    f"组织架构第{i}条：研发部负责核心算法，产品部负责需求设计，运营部负责增长留存。" for i in range(1, 40)
)
_PARTIAL_C = "第三章 售后服务\n" + " ".join(
    f"售后第{i}项：7x24小时技术支持覆盖北上广深，支持电话、邮件与远程调试。" for i in range(1, 40)
)

V_P1 = "\n\n".join([_PARTIAL_A, _PARTIAL_B1, _PARTIAL_C])
V_P2 = "\n\n".join([_PARTIAL_A, _PARTIAL_B2, _PARTIAL_C])


def test_incremental_partial_update_preserves_chunk_indexes(tmp_path: Path) -> None:
    """部分块更新：未变块 doc_id 保留，变更块写原始 chunk_index（跨库一致）。"""
    from app.db.models import Document
    from app.db.postgres import SessionLocal
    from app.rag import vector_store
    from app.rag.ingestion import ingest_file

    doc = tmp_path / "kb_partial.txt"
    source = str(doc.resolve())
    try:
        doc.write_text(V_P1, encoding="utf-8")
        r1 = ingest_file(doc, user_id="default")
        assert r1.get("chunks", 0) >= 3, f"v1 应产生至少 3 个块: {r1}"

        def _pg_rows() -> list[tuple[str, int, str]]:
            with SessionLocal() as db:
                return [
                    (d.id, d.chunk_index, d.text)
                    for d in db.query(Document)
                    .filter(Document.source == source)
                    .order_by(Document.chunk_index.asc())
                    .all()
                ]

        v1 = _pg_rows()
        assert [ci for _, ci, _ in v1] == list(range(len(v1))), (
            f"v1 chunk_index 应为连续序号: {v1}"
        )

        # ---- v2：只改第二章（原始序号 1），第一/三章不变 ----
        doc.write_text(V_P2, encoding="utf-8")
        r2 = ingest_file(doc, user_id="default")
        assert not r2.get("unchanged"), f"v2 应产生增量写入: {r2}"
        v2 = _pg_rows()
        assert len(v2) == len(v1), f"块数不应变化: {len(v1)} -> {len(v2)}"
        # Postgres 侧必须保留原始 chunk_index；若写批内位置，这里会出现重复序号
        assert [ci for _, ci, _ in v2] == list(range(len(v2))), (
            f"v2 的 Postgres chunk_index 必须保留原始序号（否则与 Milvus 失配）: {v2}"
        )

        # 未变块（第一/三章）doc_id 保留；变更块（第二章）换新 id
        v1_by_text = {t: (i, ci) for i, ci, t in v1}
        for i2, ci2, t in v2:
            if t in v1_by_text:
                assert i2 == v1_by_text[t][0], f"未变块的 doc_id 应保留: {t[:30]}"
                assert ci2 == v1_by_text[t][1]
        b1 = next((i, t) for i, ci, t in v1 if "财务部" in t)
        b2 = next((i, t) for i, ci, t in v2 if "研发部" in t)
        assert b1[0] != b2[0], "变更块应生成新的 doc_id"
        assert b1[1] != b2[1], "变更块内容应更新"

        # Milvus 收敛后必须与 Postgres 的 (doc_id, chunk_index) 完全一致
        pg_pairs = sorted((i, ci) for i, ci, _ in v2)
        mv = wait_milvus_converged(source, pg_pairs)
        assert sorted(mv) == pg_pairs, (
            f"Milvus/Postgres 融合键不一致（幽灵向量残留或 chunk_index 错位）\n"
            f"PG: {pg_pairs}\nMV: {sorted(mv)}"
        )
    finally:
        vector_store.delete_by_source(source, user_id="default")
        wait_milvus_converged(source, [])  # 等删除收敛，防止幽灵向量累积挤占 top-K
        with SessionLocal() as db:
            db.query(Document).filter(Document.source == source).delete(
                synchronize_session=False
            )
            db.commit()
