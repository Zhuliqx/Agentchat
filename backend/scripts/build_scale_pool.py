# -*- coding: utf-8 -*-
"""把 CRUD-RAG 80000_docs 抽成"每行一文件"的规模测试池。

用途：文档池扩容实验（800 → 2k → 1万 → 全量），配合 eval_rag 观察 MRR
随规模衰减。--exclude-dir 传入评估语料目录，可剔除与目标文档重复的新闻，
避免"同一篇新闻以池子里的路径被命中"把来源命中误判成未命中。

用法：
    python scripts/build_scale_pool.py --src C:\\path\\crud_rag\\80000_docs \\
        --out C:\\path\\scale_pool --limit 1200 \\
        --exclude-dir ..\\data\\eval_corpus\\crud_1docs
    python scripts/build_scale_pool.py --src ... --out ...   # 不限量 = 全池
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import setup_utf8_stdio
from app.evaluation.hallu import make_probe, normalize_text


def iter_pool_lines(src: Path) -> Iterator[str]:
    """按分片名排序逐行读池子（空白归一化后非空）。"""
    for f in sorted(src.glob("documents_dup_part_*")):
        for line in f.read_text(encoding="utf-8").split("\n"):
            norm = normalize_text(line)
            if norm:
                yield norm


def load_probes(dirs: Iterable[Path]) -> list[str]:
    """把排除目录下每篇文档的前 60 字作为去重探针。"""
    probes: list[str] = []
    for d in dirs:
        for f in sorted(d.glob("*.txt")):
            probe = make_probe(f.read_text(encoding="utf-8"))
            if probe:
                probes.append(probe)
    return probes


def select_lines(lines: Iterable[str], probes: list[str], limit: int | None) -> list[str]:
    """过滤与目标重复的行，并截断到 limit（None=不限）。"""
    out: list[str] = []
    for line in lines:
        if probes and any(p in line for p in probes):
            continue
        out.append(line)
        if limit is not None and len(out) >= limit:
            break
    return out


def main() -> None:
    setup_utf8_stdio()
    ap = argparse.ArgumentParser(description="构建规模测试池（每行一文件）")
    ap.add_argument("--src", required=True, help="80000_docs 目录（documents_dup_part_* 分片）")
    ap.add_argument("--out", required=True, help="输出目录（pool_000001.txt ...）")
    ap.add_argument("--limit", type=int, default=0, help="最多写多少行（0=全量）")
    ap.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="排除与这些目录中文档重复的新闻（可多次指定）",
    )
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    probes = load_probes(Path(d) for d in args.exclude_dir)
    limit = args.limit or None
    lines = select_lines(iter_pool_lines(src), probes, limit)

    out.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(lines, start=1):
        (out / f"pool_{i:06d}.txt").write_text(line + "\n", encoding="utf-8")
    print(f"规模池: 写入 {len(lines)} 篇 → {out}（去重探针 {len(probes)} 条）")


if __name__ == "__main__":
    main()
