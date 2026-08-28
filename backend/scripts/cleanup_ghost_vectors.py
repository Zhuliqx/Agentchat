"""清理存量幽灵向量（一次性维护脚本）。

做法：对 Postgres 中每个 (user_id, source) 调 ``ingest_file(force_reingest=True)``
（按 source 删旧向量再全量重建，delete_by_source 会把孤儿向量一并清掉）。

用法:
    python scripts/cleanup_ghost_vectors.py
    python scripts/cleanup_ghost_vectors.py --dry-run   # 只列出将重建的文档
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.postgres import SessionLocal, distinct_document_sources
from app.db.models import Document
from app.rag import vector_store


def main() -> None:
    parser = argparse.ArgumentParser(description="清理存量幽灵向量（force_reingest 全量重建）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将重建的文档，不实际执行")
    args = parser.parse_args()

    pairs = distinct_document_sources()
    if not pairs:
        print("Postgres 中没有文档记录。")
        return
    print(f"将处理 {len(pairs)} 个 (user, source)：")

    ok = errors = skipped = 0
    for user_id, source in pairs:
        path = Path(source)
        if not path.exists():
            # 源文件已丢失：直接按 source 删除残留向量 + 元数据（等价 vacuum 修复）
            print(f"[源文件缺失] user={user_id} source={source} → 清理残留")
            if args.dry_run:
                skipped += 1
                continue
            try:
                vector_store.delete_by_source(source, user_id=user_id)
                with SessionLocal() as db:
                    n = (
                        db.query(Document)
                        .filter(Document.source == source, Document.user_id == user_id)
                        .delete(synchronize_session=False)
                    )
                    db.commit()
                print(f"  已清理 {n} 条元数据 + 该 source 全部向量")
                ok += 1
            except Exception as exc:
                print(f"  清理失败: {exc}")
                errors += 1
            continue

        print(f"[重建] user={user_id} {source}")
        if args.dry_run:
            skipped += 1
            continue
        try:
            from app.rag.ingestion import ingest_file

            r = ingest_file(path, user_id=user_id, force_reingest=True)
            print(f"  完成: {r.get('chunks', 0)} 个分块（unchanged={r.get('unchanged')}）")
            ok += 1
        except Exception as exc:
            print(f"  重建失败: {exc}")
            errors += 1

    print(f"\n完成: 成功 {ok} / 失败 {errors}" + (f" / 跳过(dry-run) {skipped}" if args.dry_run else ""))


if __name__ == "__main__":
    main()
