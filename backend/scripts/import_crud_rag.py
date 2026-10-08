# -*- coding: utf-8 -*-
"""把 CRUD-RAG 的 1doc 问答集转成本项目的评估语料 + GT。

数据来源（Apache-2.0）：https://github.com/IAAR-Shanghai/CRUD_RAG
需要这些文件（从仓库 raw 下载）：
  - data/crud_split/split_merged.json                     # 文档正文（各类任务的 news1/2/3）
  - src/quest_eval/QuestAnswer1Doc_quest_gt_save.json     # 1doc 问题 + 答案（按 doc id 索引）
  - src/quest_eval/QuestAnswer2Docs_quest_gt_save.json
  - src/quest_eval/QuestAnswer3Docs_quest_gt_save.json

产出：
  - data/eval_corpus/crud_<task>/...               # 每篇文档一个文件
  - data/eval_corpus/crud_<task>_gt.json           # 全量 GT
  - data/eval_corpus/crud_<task>_gt_sample.json    # 抽样 GT（--sample-size）

用法：
    python scripts/import_crud_rag.py --src C:\\path\\to\\crud_rag
    python scripts/import_crud_rag.py --src C:\\path\\to\\crud_rag --task 2docs

注意：1/2/3doc 三组任务对**同一个事件**给的是**不同媒体的不同报道**（实测 news1 不相等），
所以三组各自独立成池——混在一个池里会让"同事件其他报道"挤掉正解、误判为未命中。
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data" / "eval_corpus"

TASKS = {
    "1docs": ("questanswer_1doc", ["news1"], "gt_1doc.json"),
    "2docs": ("questanswer_2docs", ["news1", "news2"], "gt_2docs.json"),
    "3docs": ("questanswer_3docs", ["news1", "news2", "news3"], "gt_3docs.json"),
}


def _load(src: Path, task: str) -> tuple[list[dict], dict]:
    split_key, fields, gt_file = TASKS[task]
    split = json.loads((src / "split_merged.json").read_text(encoding="utf-8"))
    gt = json.loads((src / gt_file).read_text(encoding="utf-8"))
    records = [r for r in split[split_key] if r.get("ID")]
    return records, gt


def _doc_name(task: str, doc_id: str, slot: int) -> str:
    """1doc 沿用 <ID>.txt（保持既有语料与 GT 路径不变），多文档任务加槽位后缀。"""
    return f"{doc_id}.txt" if task == "1docs" else f"{doc_id}__n{slot + 1}.txt"


def build(src: Path, out_root: Path, sample_size: int, task: str) -> None:
    _, fields, _ = TASKS[task]
    records, gt = _load(src, task)
    corpus_dir = out_root / f"crud_{task}"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    docs: dict[str, list[str]] = {
        r["ID"]: [r.get(f) or "" for f in fields] for r in records
    }
    # GT 的 id 数略多于 split 的记录数（2/3docs 各多 3 个）——缺文档的条目直接跳过
    gt = {i: v for i, v in gt.items() if i in docs}
    skipped = len(records) - len(gt) if len(gt) < len(records) else 0
    if skipped:
        print(f"[{task}] 跳过 {skipped} 条 GT（其文档不在 split 记录里）")

    written = 0
    for doc_id, texts in docs.items():
        for slot, text in enumerate(texts):
            if not text.strip():
                continue
            (corpus_dir / _doc_name(task, doc_id, slot)).write_text(
                text.strip() + "\n", encoding="utf-8"
            )
            written += 1

    cases = []
    for doc_id, item in gt.items():
        paths = [
            str((corpus_dir / _doc_name(task, doc_id, slot)).resolve())
            for slot in range(len(docs.get(doc_id, [])))
            if (corpus_dir / _doc_name(task, doc_id, slot)).exists()
        ]
        answers = item.get("answers") or []
        for i, question in enumerate(item.get("question") or []):
            cases.append(
                {
                    "id": f"{doc_id}-q{i}",
                    "question": question,
                    "answer": answers[i] if i < len(answers) else "",
                    "expected_sources": paths,
                    "notes": f"CRUD-RAG {task}（Apache-2.0）",
                }
            )

    full = {
        "name": f"crud-rag-{task}",
        "note": (
            "CRUD-RAG 问答；expected_sources 为本地语料绝对路径。"
            f"本任务每题对应 {len(fields)} 篇支撑文档，命中任一即算召回（eval_rag 的 _hit 语义）。"
        ),
        "cases": cases,
    }
    (out_root / f"crud_{task}_gt.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sample = dict(full)
    sample["name"] = f"crud-rag-{task}-sample"
    sample["cases"] = random.Random(20261008).sample(cases, min(sample_size, len(cases)))
    (out_root / f"crud_{task}_gt_sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total_chars = sum(len(t) for texts in docs.values() for t in texts)
    print(f"[{task}] 文档 {written} 篇 / {total_chars} 字符 → {corpus_dir}")
    print(f"[{task}] 全量 GT {len(cases)} 问 → crud_{task}_gt.json")
    print(f"[{task}] 抽样 GT {len(sample['cases'])} 问 → crud_{task}_gt_sample.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="导入 CRUD-RAG 1doc 作为评估语料")
    ap.add_argument("--src", required=True, help="放着 split_merged.json 与 gt_1doc.json 的目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 data/eval_corpus）")
    ap.add_argument("--sample-size", type=int, default=300, help="抽样 GT 的题量")
    ap.add_argument("--task", default="1docs", choices=sorted(TASKS), help="任务类型")
    args = ap.parse_args()
    build(Path(args.src), Path(args.out), args.sample_size, args.task)


if __name__ == "__main__":
    main()
