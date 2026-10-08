# -*- coding: utf-8 -*-
"""把 LongBench 中文子集转成本项目的检索评估语料 + GT。

数据来源：THUDM/LongBench（https://github.com/THUDM/LongBench，数据在其 HuggingFace
仓库的 data.zip；子任务各自源自上游数据集，仅作本地评估）。处理 3 个"有可判定支撑文档"的子集：
  - dureader：多文档问答，context 由 20 篇"文章N"拼接 → 支撑文档 = 含答案的文章
  - multifieldqa_zh：长文档问答，context 为一篇长文 → 支撑文档 = 该文档
  - passage_retrieval_zh：段落检索，context 为编号段落，答案为"段落N" → 支撑 = 对应段落
vcsum（会议摘要）没有单一支撑段落，不适用检索级评测，跳过。

产出（--out，默认仓库根 data/eval_corpus）：
  - longbench_{task}/          # 每篇文档一个 txt
  - longbench_{task}_gt.json   # cases.expected_sources 指向本地绝对路径，可直接喂 eval_rag
  - longbench_{task}_gt_sample.json

用法：
    python scripts/import_longbench.py --src C:\\path\\to\\longbench\\data\\data
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import setup_utf8_stdio
from app.evaluation.hallu import normalize_text

DEFAULT_OUT = PROJECT_ROOT / "data" / "eval_corpus"
TASKS = ("dureader", "multifieldqa_zh", "passage_retrieval_zh")

_ARTICLE_RE = re.compile(r"^文章\d+\s*$", re.M)
_PARAGRAPH_RE = re.compile(r"段落(\d+)：")
_TRAILING_PUNCT_RE = re.compile(r"[。！？!?；;，,、：:…\s]+$")


def sanitize(text: str) -> str:
    """去掉 NUL（0x00）：PG text 字段不接受，LongBench 部分段落含坏字符。"""
    return (text or "").replace("\x00", "")


def split_articles(context: str) -> list[str]:
    """dureader：按行首"文章N"切分，返回各文章正文（去首尾空白）。"""
    parts = _ARTICLE_RE.split(context or "")
    return [p.strip() for p in parts if p and p.strip()]


def split_paragraphs(context: str) -> dict[str, str]:
    """passage_retrieval_zh：返回 {段落号: 正文}。"""
    text = context or ""
    matches = list(_PARAGRAPH_RE.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1)] = text[m.end() : end].strip()
    return out


def answer_probe(answer: str, min_len: int = 4) -> str:
    """把参考答案修成可检索的探针：去尾标点；太短（易误命中）返回空串。"""
    probe = _TRAILING_PUNCT_RE.sub("", normalize_text(answer))
    return probe if len(probe) >= min_len else ""


def matching_files(texts: list[str], paths: list[str], answers: list[str]) -> list[str]:
    """返回含任一答案探针的文档路径（dureader 用；空列表=无法判定）。"""
    probes = [p for p in (answer_probe(a) for a in answers) if p]
    hits: list[str] = []
    for text, path in zip(texts, paths):
        norm = normalize_text(text)
        if any(probe in norm for probe in probes):
            hits.append(path)
    return hits


def best_matching_file(
    texts: list[str], paths: list[str], answers: list[str], min_lcs: int = 12
) -> str | None:
    """LCS 兜底标注：答案与文章的最长公共子串 ≥ min_lcs 且严格大于次优才判定。

    dureader 的参考答案是抽象式生成（措辞常与原文不同），精确子串会漏掉一半以上；
    用最长公共子串找最贴近的文章，并要求与第二名有区分度，避免把噪声文章当成支撑。
    """
    probes = [p for p in (answer_probe(a, min_len=8) for a in answers) if p]
    if not probes:
        return None
    scored: list[tuple[int, str]] = []
    for text, path in zip(texts, paths):
        norm = normalize_text(text)
        best = max(
            SequenceMatcher(None, probe, norm, autojunk=False).find_longest_match(
                0, len(probe), 0, len(norm)
            ).size
            for probe in probes
        )
        scored.append((best, path))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < min_lcs:
        return None
    if len(scored) > 1 and scored[0][0] <= scored[1][0]:
        return None
    return scored[0][1]


def build(src: Path, out_root: Path, sample_size: int) -> None:
    seed = 20261008
    for task in TASKS:
        rows = [
            json.loads(line)
            for line in (src / f"{task}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        corpus_dir = out_root / f"longbench_{task}"
        corpus_dir.mkdir(parents=True, exist_ok=True)

        cases: list[dict] = []
        skipped = 0
        for row in rows:
            cid = str(row.get("_id") or "").strip()
            question = str(row.get("input") or "").strip()
            answers = [str(a).strip() for a in (row.get("answers") or []) if str(a).strip()]
            if not cid or not question or not answers:
                skipped += 1
                continue

            if task == "dureader":
                texts = split_articles(row.get("context") or "")
                paths: list[str] = []
                for i, text in enumerate(texts):
                    p = corpus_dir / f"{cid}_a{i + 1:02d}.txt"
                    p.write_text(sanitize(text) + "\n", encoding="utf-8")
                    paths.append(str(p.resolve()))
                expected = matching_files(texts, paths, answers)
                if not expected:
                    best = best_matching_file(texts, paths, answers)
                    expected = [best] if best else []
            elif task == "multifieldqa_zh":
                text = (row.get("context") or "").strip()
                if not text:
                    skipped += 1
                    continue
                p = corpus_dir / f"{cid}.txt"
                p.write_text(sanitize(text) + "\n", encoding="utf-8")
                expected = [str(p.resolve())]
            else:  # passage_retrieval_zh
                paragraphs = split_paragraphs(row.get("context") or "")
                paths_by_no: dict[str, str] = {}
                for no, text in paragraphs.items():
                    p = corpus_dir / f"{cid}_p{int(no):02d}.txt"
                    p.write_text(sanitize(text) + "\n", encoding="utf-8")
                    paths_by_no[no] = str(p.resolve())
                m = re.fullmatch(r"段落\s*(\d+)", answers[0])
                expected = [paths_by_no[m.group(1)]] if m and m.group(1) in paths_by_no else []

            if not expected:
                skipped += 1
                continue
            cases.append(
                {
                    "id": cid,
                    "question": question,
                    "answer": answers[0],
                    "expected_sources": expected,
                    "notes": f"LongBench {task}（支撑文档由脚本按答案/段落号标注）",
                }
            )

        full = {
            "name": f"longbench-{task}",
            "note": "LongBench 中文子集；expected_sources 为本地语料绝对路径（source 模式判定）。",
            "cases": cases,
        }
        (out_root / f"longbench_{task}_gt.json").write_text(
            json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sample = dict(full)
        sample["name"] = f"longbench-{task}-sample"
        sample["cases"] = random.Random(seed).sample(cases, min(sample_size, len(cases)))
        (out_root / f"longbench_{task}_gt_sample.json").write_text(
            json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"[{task}] 文档 {sum(1 for _ in corpus_dir.glob('*.txt'))} 篇 / "
            f"GT {len(cases)} 条（跳过 {skipped}）→ longbench_{task}_gt.json"
        )


def main() -> None:
    setup_utf8_stdio()
    ap = argparse.ArgumentParser(description="导入 LongBench 中文子集作为检索评估语料")
    ap.add_argument("--src", required=True, help="放着 dureader.jsonl 等文件的目录")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 data/eval_corpus）")
    ap.add_argument("--sample-size", type=int, default=200, help="抽样 GT 的题量（默认 200）")
    args = ap.parse_args()
    build(Path(args.src), Path(args.out), args.sample_size)


if __name__ == "__main__":
    main()
