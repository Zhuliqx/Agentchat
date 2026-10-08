# -*- coding: utf-8 -*-
"""把 XFUND 中文子集转成"图文双通道"检索评估语料 + GT。

数据来源：XFUND（https://github.com/doc-analysis/XFUND，ACL 2021 多语言表单理解）——
标注 json 与扫描图在 GitHub Releases v1.0（zh.val.zip / zh.val.json）。本机直连 GitHub
releases 不稳，可给下载 URL 加 https://gh-proxy.com/ 前缀加速。

任务口径：每份扫描表单 = 一个文档（图片转单页 PDF，供 IMAGE_DUAL_CHANNEL 摄入）；
GT 用字段键值对：问题 = 字段名（question 实体），答案 = 字段值（linked answer 实体），
期望命中文档 = 该表单 PDF 的图片块（expected_images: "<pdf 绝对路径>#0"）。

产出（--out，默认仓库根 data/eval_corpus）：
  - xfund_zh/          # 每份表单一个单页 PDF
  - xfund_zh_gt.json   # cases.expected_images 指向 PDF#0（eval_rag 图片命中判定）

用法：
    python scripts/import_xfund.py --json C:\\...\\zh.val.json --images C:\\...\\images
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import setup_utf8_stdio

DEFAULT_OUT = PROJECT_ROOT / "data" / "eval_corpus"


def build_pairs(document: list[dict]) -> list[tuple[str, str]]:
    """从 question 实体的 linking 里解析 (字段名, 字段值)，去重且跳过空文本。"""
    by_id = {e.get("id"): e for e in document if isinstance(e.get("id"), int)}
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ent in document:
        if ent.get("label") != "question":
            continue
        for link in ent.get("linking") or []:
            if not isinstance(link, list) or len(link) != 2 or link[0] != ent.get("id"):
                continue
            target = by_id.get(link[1])
            if not target or target.get("label") != "answer":
                continue
            q = str(ent.get("text") or "").strip()
            a = str(target.get("text") or "").strip()
            if q and a and (q, a) not in seen:
                seen.add((q, a))
                pairs.append((q, a))
    return pairs


def image_to_pdf(image_path: Path, pdf_path: Path, dpi: int = 300) -> None:
    """单张扫描图 → 单页 PDF（300 dpi：保留可读性、控制体积）。"""
    from PIL import Image

    with Image.open(image_path) as img:
        img.convert("RGB").save(pdf_path, "PDF", resolution=dpi)
    # 空文件视为失败：PIL 保存异常时可能留下 0 字节文件
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"PDF 写入失败: {pdf_path}")


def build(json_path: Path, images_dir: Path, out_root: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    corpus_dir = out_root / "xfund_zh"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    cases: list[dict] = []
    title_cases: list[dict] = []
    skipped_docs = 0
    for doc in data.get("documents") or []:
        uid = str(doc.get("uid") or doc.get("id") or "").strip()
        fname = str((doc.get("img") or {}).get("fname") or "").strip()
        image_path = images_dir / fname
        if not uid or not image_path.exists():
            skipped_docs += 1
            continue
        pdf_path = corpus_dir / f"{uid}.pdf"
        image_to_pdf(image_path, pdf_path)
        pairs = build_pairs(doc.get("document") or [])
        for i, (q, a) in enumerate(pairs):
            cases.append(
                {
                    "id": f"{uid}-f{i:02d}",
                    "question": q,
                    "answer": a,
                    "expected_images": [f"{pdf_path.resolve()}#0"],
                    "notes": "XFUND zh：扫描表单字段问答（图片通道命中判定）",
                }
            )
        # 标题语义查询（每份表单 1 条）：表头优先，回退为前 3 个字段名组合
        entities = doc.get("document") or []
        headers = [
            str(e.get("text") or "").strip()
            for e in entities
            if e.get("label") == "header" and str(e.get("text") or "").strip()
        ]
        if headers:
            query = " ".join(dict.fromkeys(headers))[:120]
        else:
            labels = [
                str(e.get("text") or "").strip()
                for e in entities
                if e.get("label") == "question" and str(e.get("text") or "").strip()
            ]
            query = "、".join(dict.fromkeys(labels))[:120]
        if query:
            title_cases.append(
                {
                    "id": f"{uid}-title",
                    "question": query,
                    "answer": "",
                    "expected_images": [f"{pdf_path.resolve()}#0"],
                    "notes": "XFUND zh：表单标题/字段组合语义查询（图片通道）",
                }
            )

    payload = {
        "name": "xfund-zh",
        "note": (
            "XFUND 中文表单：每份表单一个单页 PDF；expected_images 为 <pdf>#0（图片块索引），"
            "评测需 IMAGE_DUAL_CHANNEL=true（摄入与检索同一开关）。"
        ),
        "cases": cases,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "xfund_zh_gt.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_root / "xfund_zh_title_gt.json").write_text(
        json.dumps(
            {
                "name": "xfund-zh-title",
                "note": payload["note"],
                "cases": title_cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"[xfund] PDF {len(list(corpus_dir.glob('*.pdf')))} 份 / 字段 GT {len(cases)} 条"
        f"（跳过 {skipped_docs} 份）→ xfund_zh_gt.json；标题 GT {len(title_cases)} 条"
        f" → xfund_zh_title_gt.json"
    )


def main() -> None:
    setup_utf8_stdio()
    ap = argparse.ArgumentParser(description="导入 XFUND 中文子集作为图文双通道评估语料")
    ap.add_argument("--json", required=True, help="zh.val.json（或 zh.train.json）")
    ap.add_argument("--images", required=True, help="解压后的图片目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 data/eval_corpus）")
    args = ap.parse_args()
    build(Path(args.json), Path(args.images), Path(args.out))


if __name__ == "__main__":
    main()
