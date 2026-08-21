"""RAG 检索回归集成测试（需 Postgres + Milvus + embedding 模型）。

用途：在 CI（rag-regression job）或本地 Docker 环境中，摄入固定测试文档后
断言检索命中率不低于阈值，防止"改检索代码/参数导致质量退化"。

DB 不可达时自动跳过（与 tests/test_api.py 同模式），不阻塞纯单元测试。

运行（需 Docker 依赖已启动）：
    cd backend
    .\\venv\\Scripts\\python.exe -m pytest tests/test_rag_regression.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KB_DOC = FIXTURES / "kb_ci.txt"

# 检索回归案例：(问题, 期望关键词列表)——基于 kb_ci.txt 内容
CASES: list[tuple[str, list[str]]] = [
    ("公司成立于哪一年？", ["2021"]),
    ("公司总部位于哪个城市？", ["上海"]),
    ("公司有多少名员工？", ["80"]),
    ("公司的旗舰产品是什么？", ["智能问答引擎"]),
    ("公司支持哪些部署方式？", ["私有化"]),
    ("公司的联系电话是多少？", ["021-66666666"]),
    ("公司的地址在哪里？", ["张江"]),
]

# 命中率阈值：小文档 + 精确关键词，期望全部命中；低于 0.85 视为检索退化
MIN_HIT_RATE = 0.85
TOP_K = 3


def _db_available() -> bool:
    """检查 Postgres 与 Milvus 是否可达（不可达则跳过本测试）。"""
    try:
        from app.db.postgres import SessionLocal
        from app.rag.vector_store import _client

        with SessionLocal() as db:
            from sqlalchemy import text

            db.execute(text("SELECT 1"))
        _client().list_collections()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="Postgres/Milvus 不可用（需 Docker 依赖）"
)


def _ingest_once() -> int:
    """摄入 kb_ci.txt（幂等：内容不变则 unchanged，不重复写入）。"""
    from app.rag.ingestion import ingest_file

    result = ingest_file(KB_DOC, user_id="default")
    chunks = result.get("chunks", 0)
    assert chunks > 0 or result.get("unchanged"), f"摄入失败: {result}"
    # 若 unchanged 说明此前已摄入，chunks=0 属正常；返回一个正数表示可用
    return chunks or 1


def test_retrieval_hit_rate_regression() -> None:
    """固定案例集上检索命中率 ≥ 阈值（防检索质量退化）。"""
    from app.rag import hybrid

    _ingest_once()
    hit = 0
    details: list[str] = []
    for q, kws in CASES:
        hits = hybrid.search_hybrid(q, top_k=TOP_K)
        joined = " ".join(
            str(h.get("text", "")) + " " + str(h.get("source", "")) for h in hits
        ).lower()
        ok = any(k.lower() in joined for k in kws)
        hit += ok
        details.append(f"{'OK' if ok else 'X'} {q} -> top{len(hits)}")

    rate = hit / len(CASES)
    print("\n".join(details))
    print(f"\n命中率: {hit}/{len(CASES)} = {rate:.2f}（阈值 {MIN_HIT_RATE}）")
    assert rate >= MIN_HIT_RATE, (
        f"检索回归未通过: 命中率 {rate:.2f} < {MIN_HIT_RATE}；"
        "请检查检索链路（分块/混合检索/索引）是否退化。"
    )
