"""文档摄入命令行工具。

用法:
    python scripts/ingest_docs.py path/to/file.txt
    python scripts/ingest_docs.py path/to/folder/
    python scripts/ingest_docs.py folder --pattern "*.md"
    python scripts/ingest_docs.py file.txt --user zhu   # 摄入到指定用户的知识库

支持: txt / md / pdf / docx / html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.ingestion import ingest_directory, ingest_file


def main() -> None:
    parser = argparse.ArgumentParser(description="摄入文档到 RAG 向量库")
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument("--pattern", default="*.{txt,md,pdf,docx,html}", help="目录匹配模式")
    parser.add_argument(
        "--user",
        default="default",
        help="知识库归属用户 id（默认 default=访客/未登录用户）",
    )
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_dir():
        results = ingest_directory(p, args.pattern, user_id=args.user)
    elif p.is_file():
        results = [ingest_file(p, user_id=args.user)]
    else:
        print(f"路径不存在: {p}")
        sys.exit(1)

    ok = 0
    for r in results:
        if "error" in r:
            print(f"[失败] {r.get('filename')}: {r['error']}")
        else:
            print(f"[成功] {r['filename']}: {r['chunks']} 个分块")
            ok += 1
    print(f"\n完成: {ok}/{len(results)} 个文件摄入。")


if __name__ == "__main__":
    main()
