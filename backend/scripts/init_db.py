"""初始化数据库：Alembic 迁移 Postgres Schema + 确保 Milvus collection 存在。

用法:
    python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.postgres import init_db, run_migrations
from app.rag.vector_store import ensure_vector_store


def main() -> None:
    print("==> Alembic 迁移到最新版本...")
    run_migrations()
    print("==> Alembic OK")
    print("==> 确保 Postgres 内置访客用户...")
    init_db()
    print("==> Postgres OK")

    print("==> 初始化 Milvus collection...")
    ensure_vector_store()
    print("==> Milvus OK")

    print("数据库初始化完成。")


if __name__ == "__main__":
    main()
