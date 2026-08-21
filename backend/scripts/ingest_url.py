"""从 URL 抓取网页并摄入到 RAG 知识库。

用法:
    python scripts/ingest_url.py https://example.com/article
    python scripts/ingest_url.py https://example.com --user zhu   # 摄入到指定用户知识库

原理:
    抓取 HTML → 持久保存到 data/uploads/urls/<uuid>.html → 复用现有
    ingest_file（内置 HTML → 纯文本解析），与网页上传同链路（可预览/删除）。
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT, settings
from app.rag.ingestion import ingest_file


def fetch_html(url: str, timeout: float = 20.0) -> str:
    """抓取 URL 返回 HTML 文本；失败抛异常。"""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except ImportError:
        # langchain 传递依赖通常已带 httpx；缺时回退标准库
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取网页并摄入到 RAG 向量库")
    parser.add_argument("url", help="网页 URL")
    parser.add_argument("--user", default="default", help="知识库归属用户 id")
    args = parser.parse_args()

    html = fetch_html(args.url)
    parsed = urlparse(args.url)

    # 持久保存原始 HTML（与网页上传同目录，可预览/删除）
    dest_dir = PROJECT_ROOT / settings.upload_dir / "urls"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}.html"
    dest.write_text(html, encoding="utf-8")

    filename = f"{parsed.netloc}{parsed.path or '/'}"
    result = ingest_file(dest, filename=filename, user_id=args.user)

    if "error" in result:
        print(f"[失败] {args.url}: {result['error']}")
        sys.exit(1)
    print(
        f"[成功] {args.url}\n"
        f"  文件名: {result.get('filename')}\n"
        f"  分块数: {result.get('chunks')}\n"
        f"  用户:   {args.user}\n"
        f"  源文件: {result.get('source')}"
    )


if __name__ == "__main__":
    main()
