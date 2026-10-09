# -*- coding: utf-8 -*-
"""把 CRUD-RAG 幻觉纠正任务（hallu_modified）转成本项目评估语料 + GT。

数据来源（Apache-2.0）：https://github.com/IAAR-Shanghai/CRUD_RAG
需要（--src 目录下）：
  - split_merged.json                         # 顶层 key hallu_modified（1268 条案例）
  - hallu_docs/documents_hallu.txt_part_1..3   # 检索库正文（每行一条，共 5130 行）

产出（--out，默认仓库根 data/eval_corpus）：
  - crud_hallu_db/db_XXXX.txt    # 检索库（用 ingest_docs.py --batch 摄入）
  - crud_hallu_gt.json           # 全量 GT（约 1268 条）
  - crud_hallu_gt_sample.json    # 抽样 GT（--sample-size）

用法：
    python scripts/import_crud_hallu.py --src C:\\path\\to\\crud_rag
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import setup_utf8_stdio
from app.evaluation.hallu import make_probe, normalize_text

DEFAULT_OUT = PROJECT_ROOT / "data" / "eval_corpus"
_DB_PARTS = (
    "documents_hallu.txt_part_1",
    "documents_hallu.txt_part_2",
    "documents_hallu.txt_part_3",
)


def load_db_lines(src: Path) -> list[str]:
    """读检索库分片：每行一条（空白归一化后非空）。"""
    lines: list[str] = []
    for name in _DB_PARTS:
        text = (src / "hallu_docs" / name).read_text(encoding="utf-8")
        for line in text.split("\n"):
            norm = normalize_text(line)
            if norm:
                lines.append(norm)
    return lines


def build_cases(rows: list[dict], db_lines: list[str]) -> tuple[list[dict], int]:
    """把 hallu_modified 原始记录转成 GT 案例；返回 (cases, skipped)。

    跳过条件：关键字段缺失 / 生成失败占位 / 正确续写在检索库中找不到（探针失配）。
    """
    db = "\n".join(db_lines)
    cases: list[dict] = []
    skipped = 0
    for row in rows:
        begin = (row.get("newsBeginning") or "").strip()
        hallu = (row.get("hallucinatedContinuation") or "").strip()
        ref = (row.get("hallucinatedMod") or "").strip()
        if not begin or not hallu or not ref or "request openai failed" in ref:
            skipped += 1
            continue
        probe = make_probe(row.get("newsRemainder") or "")
        if not probe or probe not in db:
            skipped += 1
            continue
        bad = [k for k, v in (row.get("allKeywords") or {}).items() if str(v).startswith("不合理")]
        cases.append(
            {
                "id": row["ID"],
                "news_beginning": begin,
                "hallucinated_continuation": hallu,
                "reference_corrected": ref,
                "bad_keywords": bad,
                "remainder_probe": probe,
                "notes": (
                    f"CRUD-RAG hallu_modified（Apache-2.0）；"
                    f"幻觉由 {row.get('generatedBy', '未知模型')} 生成"
                ),
            }
        )
    return cases, skipped


def main() -> None:
    setup_utf8_stdio()
    ap = argparse.ArgumentParser(description="导入 CRUD-RAG hallu_modified 作为幻觉纠正评估集")
    ap.add_argument("--src", required=True, help="放着 split_merged.json 与 hallu_docs/ 的目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 data/eval_corpus）")
    ap.add_argument("--sample-size", type=int, default=200, help="抽样 GT 的题量（默认 200）")
    args = ap.parse_args()

    src = Path(args.src)
    out_root = Path(args.out)
    rows = json.loads((src / "split_merged.json").read_text(encoding="utf-8"))["hallu_modified"]
    db_lines = load_db_lines(src)
    cases, skipped = build_cases(rows, db_lines)

    db_dir = out_root / "crud_hallu_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(db_lines, start=1):
        (db_dir / f"db_{i:04d}.txt").write_text(line + "\n", encoding="utf-8")

    full = {
        "name": "crud-rag-hallu-modified",
        "note": (
            "CRUD-RAG 幻觉纠正：用检索材料修正幻觉续写；reference_corrected 为人工修订参考。"
            "remainder_probe 是正确续写前 60 字，供检索命中判定。"
        ),
        "cases": cases,
    }
    (out_root / "crud_hallu_gt.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sample = dict(full)
    sample["name"] = "crud-rag-hallu-modified-sample"
    sample["cases"] = random.Random(20261008).sample(cases, min(args.sample_size, len(cases)))
    (out_root / "crud_hallu_gt_sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[hallu] 检索库 {len(db_lines)} 行 → {db_dir}")
    print(f"[hallu] GT {len(cases)} 条（跳过 {skipped}） → crud_hallu_gt.json")
    print(f"[hallu] 抽样 GT {len(sample['cases'])} 条 → crud_hallu_gt_sample.json")


if __name__ == "__main__":
    main()
