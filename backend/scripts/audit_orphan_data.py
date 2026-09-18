"""数据体检与清理：列出/清理 Postgres / Milvus 中的孤儿与冗余数据。

用法（在 backend/ 下）：
    python scripts/audit_orphan_data.py
    python scripts/audit_orphan_data.py --json-out data/eval_runs/orphan_audit.json
    python scripts/audit_orphan_data.py --apply          # 二次确认 + 自动备份后清理

默认只读；--apply 才会删除。判断项（重复消息、repro 用户文档等）默认保留，
需要显式 --include-orphan-user-docs 才会删除 repro 的孤儿用户文档。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from pymilvus import MilvusClient
from psycopg.rows import dict_row

from app.config import settings

MAIN_COLLECTION = settings.milvus_collection
IMAGE_COLLECTION = "agent_images"


def _milvus_rows(
    client: MilvusClient, collection: str, fields: list[str]
) -> list[dict]:
    """分页读取 collection 全部行（只读；count(*) 作为可信总数）。"""
    total = int(
        client.query(collection, filter="", output_fields=["count(*)"]).pop()[
            "count(*)"
        ]
    )
    rows: list[dict] = []
    page = 5000
    offset = 0
    while offset < total:
        batch = client.query(
            collection,
            filter="",
            output_fields=fields,
            limit=page,
            offset=offset,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
    return rows


def _pg_snapshot(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users")
        users = {str(r[0]) for r in cur.fetchall()}

        cur.execute("SELECT id, user_id, source, chunk_index FROM documents")
        documents = [
            {
                "id": str(r[0]),
                "user_id": str(r[1]),
                "source": str(r[2]),
                "chunk_index": int(r[3]),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT s.id, s.title, s.created_at
            FROM sessions s
            WHERE NOT EXISTS (
                SELECT 1 FROM messages m WHERE m.session_id = s.id
            )
            ORDER BY s.created_at
            """
        )
        empty_sessions = [
            {"id": str(r[0]), "title": str(r[1]), "created_at": r[2].isoformat()}
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT s.prefix, s.key, s.value, split_part(s.prefix, '.', 1) AS user_id,
                   EXISTS(
                       SELECT 1 FROM store_vectors v
                       WHERE v.prefix = s.prefix AND v.key = s.key
                   ) AS has_vector
            FROM store s
            ORDER BY s.prefix, s.key
            """
        )
        store_rows = [
            {
                "prefix": str(r[0]),
                "key": str(r[1]),
                "value": r[2],
                "user_id": str(r[3]),
                "has_vector": bool(r[4]),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT session_id, role, content, count(*) AS copies
            FROM messages
            GROUP BY session_id, role, content
            HAVING count(*) > 1
            ORDER BY copies DESC, content
            """
        )
        duplicate_messages = [
            {
                "session_id": str(r[0]),
                "role": str(r[1]),
                "content": str(r[2]),
                "copies": int(r[3]),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE sources IS NOT NULL
                    AND json_typeof(sources) = 'null') AS json_null_sources,
                count(*) FILTER (WHERE sources IS NULL) AS sql_null_sources
            FROM messages
            """
        )
        json_null_sources, sql_null_sources = cur.fetchone()

        cur.execute(
            """
            SELECT count(*) FROM documents
            WHERE vector_status = 'synced' AND vector_synced_at IS NULL
            """
        )
        synced_without_time = int(cur.fetchone()[0])

        cur.execute("SELECT key, value FROM app_settings ORDER BY key")
        settings_rows = [{"key": str(r[0]), "value": str(r[1])} for r in cur.fetchall()]

    return {
        "users": users,
        "documents": documents,
        "empty_sessions": empty_sessions,
        "store_rows": store_rows,
        "duplicate_messages": duplicate_messages,
        "json_null_sources": int(json_null_sources),
        "sql_null_sources": int(sql_null_sources),
        "synced_without_time": synced_without_time,
        "app_settings": settings_rows,
    }


def _print_samples(title: str, items: list, limit: int) -> None:
    print(f"\n[{title}] {len(items)} 条")
    for item in items[:limit]:
        print("  -", item)
    if len(items) > limit:
        print(f"  ... 其余 {len(items) - limit} 条见 --json-out 报告")


def _full_rows_for_backup(conn: psycopg.Connection) -> dict:
    """备份将被删除的完整行（恢复时可直接回插）。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT * FROM sessions s
            WHERE NOT EXISTS (
                SELECT 1 FROM messages m WHERE m.session_id = s.id
            )
            """
        )
        empty_sessions = cur.fetchall()
        cur.execute(
            """
            SELECT * FROM documents
            WHERE user_id NOT IN (SELECT id FROM users)
            """
        )
        orphan_documents = cur.fetchall()
        cur.execute(
            """
            SELECT * FROM store
            WHERE split_part(prefix, '.', 1) NOT IN (SELECT id FROM users)
            """
        )
        orphan_store = cur.fetchall()
    return {
        "empty_sessions": empty_sessions,
        "orphan_documents": orphan_documents,
        "orphan_store_rows": orphan_store,
    }


def _delete_milvus_doc_ids(
    client: MilvusClient, doc_ids: list[str], batch_size: int = 100
) -> int:
    """按 doc_id 分批删除 Milvus 行；返回提交的 doc_id 数。"""
    deleted = 0
    for start in range(0, len(doc_ids), batch_size):
        chunk = doc_ids[start : start + batch_size]
        quoted = ", ".join(f'"{doc_id}"' for doc_id in chunk)
        client.delete(MAIN_COLLECTION, filter=f"doc_id in [{quoted}]")
        deleted += len(chunk)
    return deleted


def _delete_milvus_sources(
    client: MilvusClient, collection: str, sources: list[str]
) -> None:
    for source in sources:
        escaped = source.replace("\\", "\\\\").replace('"', '\\"')
        client.delete(collection, filter=f'source == "{escaped}"')


def _compact(client: MilvusClient, collection: str, timeout: float = 60.0) -> None:
    """触发 compact 并尽量等待完成；不可用时只警告。"""
    try:
        job = client.compact(collection)
        job_id = job.get("compaction_id") if isinstance(job, dict) else job
        if job_id is None:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = client.get_compaction_state(job_id)
            name = str(getattr(state, "state", "") or state)
            if "Completed" in name:
                return
            time.sleep(1.0)
        print(f"  警告：{collection} compact 未在 {timeout:.0f}s 内完成，稍后自动继续")
    except Exception as exc:  # noqa: BLE001 - compact 失败不影响删除结果
        print(f"  警告：{collection} compact 触发失败: {exc}")


async def _repair_store_vectors(rows: list[dict], users: set[str]) -> int:
    """对「user 存在但缺向量」的 Store 行重新 aput，触发语义索引补写。"""
    from app.db.memory_store import close_store, init_store

    targets = [
        row
        for row in rows
        if row["user_id"] in users and not row["has_vector"]
    ]
    if not targets:
        return 0
    store = await init_store()
    if store is None:
        print("  警告：Store 不可用，跳过缺向量修复")
        return 0
    repaired = 0
    try:
        for row in targets:
            namespace = tuple(
                part for part in row["prefix"].strip(".").split(".") if part
            )
            await store.aput(namespace, row["key"], row["value"])
            repaired += 1
    finally:
        await close_store()
    return repaired


def _apply_cleanup(
    args: argparse.Namespace,
    conn: psycopg.Connection,
    client: MilvusClient,
    pg: dict,
    report: dict,
    orphan_doc_ids: set[str],
) -> None:
    """执行清理：先备份、再确认，然后按固定顺序删除与修复。"""
    orphan_documents = [
        d for d in pg["documents"] if d["user_id"] not in pg["users"]
    ]
    orphan_user_doc_ids = sorted({d["id"] for d in orphan_documents})
    image_orphans = report["milvus"]["image_orphan_sources"]

    print("\n=== 将执行 ===")
    print(f"Milvus 孤儿向量 doc_id: {len(orphan_doc_ids)}")
    print(f"Milvus 图片孤儿 source: {len(image_orphans)}")
    print(f"PG 空会话: {len(pg['empty_sessions'])}")
    print(
        "PG 孤儿 Store 行:",
        sum(1 for r in pg["store_rows"] if r["user_id"] not in pg["users"]),
    )
    print(
        "PG 卫生修复:",
        f"sources JSON null={pg['json_null_sources']}, "
        f"vector_synced_at 缺失={pg['synced_without_time']}",
    )
    print(
        f"孤儿用户文档（repro）: {len(orphan_user_doc_ids)} "
        f"（{'将删除' if args.include_orphan_user_docs else '本轮保留'}）"
    )
    if not args.yes:
        answer = input("确认执行请输出 DELETE：").strip()
        if answer != "DELETE":
            print("已取消，未修改任何数据。")
            return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = Path(args.backup_dir) / f"orphan_cleanup_{stamp}.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_payload = {
        "generated_at": stamp,
        "report": report,
        "pg_full_rows": _full_rows_for_backup(conn),
    }
    backup_path.write_text(
        json.dumps(backup_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"备份已写入: {backup_path}")

    deleted_vectors = _delete_milvus_doc_ids(client, sorted(orphan_doc_ids))
    if args.include_orphan_user_docs and orphan_user_doc_ids:
        deleted_vectors += _delete_milvus_doc_ids(client, orphan_user_doc_ids)
    if image_orphans:
        _delete_milvus_sources(client, IMAGE_COLLECTION, image_orphans)
    _compact(client, MAIN_COLLECTION)
    if image_orphans:
        _compact(client, IMAGE_COLLECTION)

    with conn.cursor() as cur:
        empty_ids = [s["id"] for s in pg["empty_sessions"]]
        if empty_ids:
            cur.execute("DELETE FROM sessions WHERE id = ANY(%s)", (empty_ids,))
        cur.execute(
            """
            DELETE FROM store
            WHERE split_part(prefix, '.', 1) NOT IN (SELECT id FROM users)
            """
        )
        if args.include_orphan_user_docs and orphan_user_doc_ids:
            cur.execute(
                "DELETE FROM documents WHERE id = ANY(%s)",
                (orphan_user_doc_ids,),
            )
        cur.execute(
            """
            UPDATE messages SET sources = NULL
            WHERE sources IS NOT NULL AND json_typeof(sources) = 'null'
            """
        )
        cur.execute(
            """
            UPDATE documents SET vector_synced_at = created_at
            WHERE vector_status = 'synced' AND vector_synced_at IS NULL
            """
        )
        conn.commit()

    repaired = _run_store_repair(pg)
    print(
        "清理完成："
        f"Milvus 删除 {deleted_vectors} 个 doc_id，"
        f"图片 source {len(image_orphans)} 个，"
        f"Store 向量修复 {repaired} 条。"
    )


def _run_store_repair(pg: dict) -> int:
    """运行 Store 索引修复；Windows 下切换 SelectorEventLoop（与 app.main 一致）。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(_repair_store_vectors(pg["store_rows"], pg["users"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="数据体检与清理：孤儿/冗余清单")
    parser.add_argument("--limit", type=int, default=10, help="控制台每类展示样例数")
    parser.add_argument("--json-out", default=None, help="可选：输出完整 JSON 报告路径")
    parser.add_argument(
        "--apply", action="store_true", help="执行清理（默认只读；会先备份并要求确认）"
    )
    parser.add_argument("--yes", action="store_true", help="跳过交互确认（谨慎使用）")
    parser.add_argument(
        "--backup-dir",
        default="data/eval_runs/orphan_backups",
        help="清理前的完整行备份目录",
    )
    parser.add_argument(
        "--include-orphan-user-docs",
        action="store_true",
        help="同时删除 user 不存在的孤儿文档（如 repro）",
    )
    args = parser.parse_args()

    with psycopg.connect(settings.postgres_conninfo) as conn:
        pg = _pg_snapshot(conn)

    client = MilvusClient(uri=settings.milvus_connection_uri)
    main_fields = ["doc_id", "user_id", "source", "chunk_index"]
    main_rows = _milvus_rows(client, MAIN_COLLECTION, main_fields)
    main_count = int(
        client.query(MAIN_COLLECTION, filter="", output_fields=["count(*)"]).pop()[
            "count(*)"
        ]
    )
    main_stats = int(
        client.get_collection_stats(MAIN_COLLECTION).get("row_count", 0)
    )

    pg_ids = {d["id"] for d in pg["documents"]}
    orphan_rows = [r for r in main_rows if str(r.get("doc_id")) not in pg_ids]
    orphan_by_user: dict[str, set[str]] = defaultdict(set)
    orphan_doc_ids: set[str] = set()
    for row in orphan_rows:
        doc_id = str(row.get("doc_id"))
        orphan_doc_ids.add(doc_id)
        orphan_by_user[str(row.get("user_id"))].add(doc_id)

    pair_counts = Counter(
        (str(r.get("doc_id")), int(r.get("chunk_index") or 0)) for r in main_rows
    )
    duplicate_pairs = sorted(
        [pair for pair, count in pair_counts.items() if count > 1]
    )

    image_rows: list[dict] = []
    image_orphans: list[str] = []
    if client.has_collection(IMAGE_COLLECTION):
        image_rows = _milvus_rows(
            client,
            IMAGE_COLLECTION,
            ["user_id", "source", "page", "image_index"],
        )
        pg_sources = {d["source"] for d in pg["documents"]}
        image_orphans = sorted(
            {str(r.get("source")) for r in image_rows} - pg_sources
        )

    orphan_documents = [
        d for d in pg["documents"] if d["user_id"] not in pg["users"]
    ]
    orphan_document_groups: dict[tuple[str, str], int] = Counter(
        (d["user_id"], d["source"]) for d in orphan_documents
    )
    orphan_store_rows = [
        r
        for r in pg["store_rows"]
        if r["user_id"] not in pg["users"] or not r["has_vector"]
    ]

    report = {
        "milvus": {
            "collection": MAIN_COLLECTION,
            "count_star": main_count,
            "stats_row_count": main_stats,
            "deleted_uncompacted_estimate": max(main_stats - main_count, 0),
            "orphan_vectors": len(orphan_rows),
            "orphan_doc_ids": sorted(orphan_doc_ids),
            "orphan_by_user": {
                user: sorted(ids) for user, ids in sorted(orphan_by_user.items())
            },
            "duplicate_pairs": [
                {"doc_id": doc_id, "chunk_index": idx}
                for doc_id, idx in duplicate_pairs
            ],
            "image_orphan_sources": image_orphans,
        },
        "postgres": {
            "orphan_documents": [
                {"user_id": user, "source": source, "chunks": count}
                for (user, source), count in sorted(orphan_document_groups.items())
            ],
            "empty_sessions": pg["empty_sessions"],
            "orphan_store_rows": orphan_store_rows,
            "duplicate_messages": pg["duplicate_messages"],
            "hygiene": {
                "json_null_sources": pg["json_null_sources"],
                "sql_null_sources": pg["sql_null_sources"],
                "synced_without_time": pg["synced_without_time"],
            },
        },
    }

    print("=== 数据体检（只读，不执行删除）===")
    print(
        f"Milvus: count(*)={main_count}, stats={main_stats}, "
        f"孤儿向量={len(orphan_rows)}, 重复 pair={len(duplicate_pairs)}"
    )
    print(
        "孤儿向量按用户:",
        {user: len(ids) for user, ids in sorted(orphan_by_user.items())},
    )
    _print_samples(
        "孤儿向量 doc_id",
        sorted(orphan_doc_ids),
        args.limit,
    )
    _print_samples(
        "重复 (doc_id, chunk_index)",
        [f"{d}:{i}" for d, i in duplicate_pairs],
        args.limit,
    )
    if image_rows:
        print(f"\n[图片向量] 总行数={len(image_rows)}，孤儿 source={image_orphans}")

    _print_samples(
        "PG 孤儿文档（user 不存在）",
        [
            f"{user} @ {source} ({count} chunks)"
            for (user, source), count in sorted(orphan_document_groups.items())
        ],
        args.limit,
    )
    _print_samples(
        "PG 空会话",
        [f"{s['id']} {s['created_at']}" for s in pg["empty_sessions"]],
        args.limit,
    )
    _print_samples(
        "PG 孤儿/缺索引 Store 行",
        [
            f"{r['prefix']} / {r['key']} (has_vector={r['has_vector']})"
            for r in orphan_store_rows
        ],
        args.limit,
    )
    _print_samples(
        "重复用户/助手消息（需人工判断，不自动删）",
        [
            f"{m['session_id']} {m['role']} x{m['copies']}: {m['content'][:40]}"
            for m in pg["duplicate_messages"]
        ],
        args.limit,
    )
    print(
        "\n[卫生项，不删除] "
        f"JSON null sources={pg['json_null_sources']}, "
        f"SQL null sources={pg['sql_null_sources']}, "
        f"synced_without_time={pg['synced_without_time']}"
    )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整报告已写入: {out}")

    if args.apply:
        with psycopg.connect(settings.postgres_conninfo) as conn:
            _apply_cleanup(args, conn, client, pg, report, orphan_doc_ids)


if __name__ == "__main__":
    main()
