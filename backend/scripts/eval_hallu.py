"""端到端幻觉纠正评估（CRUD-RAG hallu_modified 口径）。

用法：
    python scripts/eval_hallu.py --dataset ..\\data\\eval_corpus\\crud_hallu_gt_sample.json --user hallueval
    python scripts/eval_hallu.py ... --no-retrieval --out data/eval_runs/hallu_no_retrieval.json  # 无 RAG 基线
    python scripts/eval_hallu.py ... --input hallucinated --out ...                               # judge 灵敏度自查
    python scripts/eval_hallu.py --compare A.json B.json

说明：
- 检索走生产同路径（get_retriever），生成/评审用评估专用 LLM（get_eval_generator / judge）。
- 指标：fix_rate / residual_rate / invented_rate（LLM judge）；char_bleu & rouge-L（对人工参考）；
  bad_kw_rate（输出仍含数据集标注的"不合理"关键词）；target_hit_rate（正确续写被检索命中）。
- judge 与生成器同为 DeepSeek，绝对值有同源偏好；A/B 相对变化更可信（同 eval_quality 口径）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.evaluation import metrics, setup_utf8_stdio
from app.evaluation.hallu import (
    HalluCase,
    bad_keyword_hits,
    extract_response,
    load_hallu_cases,
    normalize_text,
)
from app.evaluation.judge_llm import (
    build_hallu_fix_prompt,
    build_hallu_judge_prompt,
    get_eval_generator,
    judge,
)
from app.rag.retriever import get_retriever

setup_utf8_stdio()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GT = PROJECT_ROOT / "data" / "eval_corpus" / "crud_hallu_gt_sample.json"
RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "eval_runs"
CONCURRENCY = 4


def _retrieve_sync(user_id: str, question: str) -> list[dict]:
    retriever = get_retriever(user_id=user_id)
    docs = retriever.invoke(question)
    return [
        {"text": d.page_content, "source": (d.metadata or {}).get("source", "")}
        for d in docs
    ]


def _target_rank(case: HalluCase, docs: list[dict]) -> int | None:
    """正确续写（前 60 字探针）在检索结果中的排名；未命中返回 None。"""
    if not case.remainder_probe:
        return None
    for i, d in enumerate(docs, start=1):
        if case.remainder_probe in normalize_text(str(d.get("text", ""))):
            return i
    return None


async def _generate_corrected(case: HalluCase, docs: list[dict]) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_eval_generator()
    sys_p, user_p = build_hallu_fix_prompt(
        case.news_beginning, case.hallucinated_continuation, docs
    )
    resp = await llm.ainvoke([SystemMessage(content=sys_p), HumanMessage(content=user_p)])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    return extract_response(text)


async def eval_case(
    case: HalluCase, *, user_id: str, use_retrieval: bool, input_mode: str
) -> dict:
    """单条 case：检索 → 生成（或直取幻觉文本）→ judge + 文本指标。"""
    record: dict[str, Any] = {"id": case.id}
    try:
        docs: list[dict] = []
        if use_retrieval:
            docs = await asyncio.to_thread(_retrieve_sync, user_id, case.news_beginning)
            rank = _target_rank(case, docs)
            record["target_rank"] = rank
            record["target_hit"] = rank is not None
        record["docs_count"] = len(docs)

        if input_mode == "hallucinated":
            candidate = case.hallucinated_continuation
        else:
            candidate = await _generate_corrected(case, docs)
        record["candidate"] = candidate

        # 文本相似度与关键词指标（无 LLM，先算）
        record["metrics.bleu_avg"] = round(
            metrics.char_bleu_avg(candidate, case.reference_corrected), 4
        )
        record["metrics.bleu_1"] = round(
            metrics.char_bleu(candidate, case.reference_corrected, 1), 4
        )
        record["metrics.rouge_l"] = round(
            metrics.rouge_l_f1(candidate, case.reference_corrected), 4
        )
        hits = bad_keyword_hits(candidate, case.bad_keywords)
        record["bad_kw_hits"] = hits
        record["metrics.bad_kw"] = 1.0 if hits else 0.0

        if not candidate.strip():
            record["empty_candidate"] = True
            record["metrics.fix"] = 0.0
            record["metrics.residual"] = 0.0
            record["metrics.invented"] = 0.0
            return record

        judged = await judge(
            build_hallu_judge_prompt(
                case.news_beginning,
                case.hallucinated_continuation,
                case.reference_corrected,
                candidate,
                docs=docs,
            )
        )
        if not judged:
            record["judge_error"] = True
            record["metrics.fix"] = None
            record["metrics.residual"] = None
            record["metrics.invented"] = None
            return record
        fix = bool(judged.get("fixes_hallucination"))
        residual = bool(judged.get("kept_false_claims"))
        invented = bool(judged.get("added_new_claims"))
        record["judged"] = {
            "fixes_hallucination": fix,
            "kept_false_claims": residual,
            "added_new_claims": invented,
        }
        record["metrics.fix"] = 1.0 if fix else 0.0
        record["metrics.residual"] = 1.0 if residual else 0.0
        record["metrics.invented"] = 1.0 if invented else 0.0
        return record
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record


# ---------------- 汇总 / 对比 ----------------

_METRIC_KEYS = [
    "metrics.fix",
    "metrics.residual",
    "metrics.invented",
    "metrics.bleu_avg",
    "metrics.bleu_1",
    "metrics.rouge_l",
    "metrics.bad_kw",
]


def _summarize(records: list[dict]) -> dict[str, Any]:
    summary: dict[str, Any] = {"cases_total": len(records), "cases_ok": 0}
    cols: dict[str, list[float]] = {k: [] for k in _METRIC_KEYS}
    hits = 0
    hit_total = 0
    for r in records:
        if r.get("error"):
            continue
        summary["cases_ok"] += 1
        for k in _METRIC_KEYS:
            v = r.get(k)
            if isinstance(v, (int, float)):
                cols[k].append(float(v))
        if isinstance(r.get("target_hit"), bool):
            hit_total += 1
            hits += 1 if r["target_hit"] else 0
    for k, vals in cols.items():
        summary[k] = round(metrics.macro_average(vals), 4) if vals else None
    summary["target_hit_rate"] = round(hits / hit_total, 4) if hit_total else None
    return summary


def _overrides() -> dict[str, Any]:
    """记录本次评估所用检索参数（写入结果文件便于复现）。"""
    return {
        "rag_top_k": settings.rag_top_k,
        "rag_score_threshold": settings.rag_score_threshold,
        "hybrid_search": settings.hybrid_search,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_candidate_k": settings.rerank_candidate_k,
        "rag_max_per_doc": settings.rag_max_per_doc,
    }


async def _run_all(
    cases: list[HalluCase], *, user_id: str, use_retrieval: bool, input_mode: str, concurrency: int
) -> list[dict]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def guarded(case: HalluCase) -> dict:
        async with sem:
            return await eval_case(
                case, user_id=user_id, use_retrieval=use_retrieval, input_mode=input_mode
            )

    return await asyncio.gather(*(guarded(c) for c in cases))


def _terminal_output(records: list[dict], summary: dict[str, Any]) -> None:
    print(f"\n通过: {summary['cases_ok']}/{summary['cases_total']}\n")
    for r in records:
        if r.get("error"):
            print(f"  [{r['id']}] [X] {r['error']}")
            continue
        fix = r.get("metrics.fix")
        fix_s = "-" if fix is None else f"{fix:.0f}"
        print(
            f"  [{r['id']}] 命中排名={r.get('target_rank', '-')} fix={fix_s} "
            f"bleu={r.get('metrics.bleu_avg', '-')} rouge-L={r.get('metrics.rouge_l', '-')} "
            f"坏词命中={len(r.get('bad_kw_hits') or [])}"
        )
    print("\n汇总 (macro):")
    for k in _METRIC_KEYS:
        print(f"  {k}: {summary.get(k)}")
    print(f"  target_hit_rate: {summary.get('target_hit_rate')}")


def _apply_overrides(args: argparse.Namespace) -> list[str]:
    changed: list[str] = []
    if args.top_k is not None:
        settings.rag_top_k = args.top_k
        changed.append(f"rag_top_k={args.top_k}")
    if args.no_rerank:
        settings.rerank_enabled = False
        changed.append("rerank_enabled=False")
    return changed


def _run(args: argparse.Namespace) -> int:
    gt_path = Path(args.dataset)
    cases = load_hallu_cases(gt_path)
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        print("GT 无案例")
        return 1

    changed = _apply_overrides(args)
    print(
        f"评估 {len(cases)} 条（数据源: {gt_path}；user={args.user}；"
        f"检索={'开' if not args.no_retrieval else '关'}；输入={args.input_mode}）"
        f"；参数覆盖: {', '.join(changed) or '无（默认配置）'}"
    )

    records = asyncio.run(
        _run_all(
            cases,
            user_id=args.user,
            use_retrieval=not args.no_retrieval,
            input_mode=args.input_mode,
            concurrency=args.concurrency,
        )
    )
    summary = _summarize(records)
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dataset": str(gt_path),
        "user": args.user,
        "use_retrieval": not args.no_retrieval,
        "input_mode": args.input_mode,
        "params": _overrides(),
    }
    _terminal_output(records, summary)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = RUNS_DIR / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"meta": meta, "cases": records, "summary": summary},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n结果已保存: {out}")
    return 0


def _compare(a: dict, b: dict) -> int:
    print(f"对比 {a['meta'].get('dataset')}  →  {b['meta'].get('dataset')}")
    print(f"  A: {json.dumps({k: a['meta'].get(k) for k in ('use_retrieval', 'input_mode')}, ensure_ascii=False)}")
    print(f"  B: {json.dumps({k: b['meta'].get(k) for k in ('use_retrieval', 'input_mode')}, ensure_ascii=False)}\n")
    print(f"{'指标':<24}{'A':>8}{'B':>8}{'Δ':>10}")
    keys = _METRIC_KEYS + ["target_hit_rate"]
    for k in keys:
        va, vb = a["summary"].get(k), b["summary"].get(k)
        if va is None or vb is None:
            print(f"{k:<24}{'-':>8}{'-':>8}{'-':>10}")
            continue
        print(f"{k:<24}{va:>8.3f}{vb:>8.3f}{vb - va:+10.3f}")

    mb = {r.get("id"): r for r in b.get("cases", [])}
    wins = ties = losses = 0
    for ra in a.get("cases", []):
        rb = mb.get(ra.get("id"))
        if not rb:
            continue
        fa, fb = ra.get("metrics.fix"), rb.get("metrics.fix")
        if not isinstance(fa, (int, float)) or not isinstance(fb, (int, float)):
            continue
        if fb > fa + 0.01:
            wins += 1
        elif fb < fa - 0.01:
            losses += 1
        else:
            ties += 1
    print(f"\nfix 逐条: 胜 {wins} / 平 {ties} / 负 {losses}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="幻觉纠正评估（CRUD-RAG hallu_modified）")
    parser.add_argument("--dataset", default=str(DEFAULT_GT), help="hallu GT json（默认抽样集）")
    parser.add_argument("--user", default="hallueval", help="评估用知识库用户 id")
    parser.add_argument("--max-cases", type=int, default=0, help="只评估前 N 条")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY, help="并发数")
    parser.add_argument("--no-retrieval", action="store_true", help="不给检索材料（无 RAG 基线）")
    parser.add_argument(
        "--input",
        dest="input_mode",
        choices=["model", "hallucinated"],
        default="model",
        help="候选来源：model=生成改写（默认）；hallucinated=直接评审原始幻觉续写（judge 自查）",
    )
    parser.add_argument("--top-k", type=int, default=None, help="覆盖 RAG_TOP_K")
    parser.add_argument("--no-rerank", action="store_true", help="关闭 rerank（A/B）")
    parser.add_argument("--out", default=None, help="结果 JSON 输出（相对路径落在 data/eval_runs/）")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="对比两份结果 JSON")
    args = parser.parse_args()

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        raise SystemExit(_compare(a, b))
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
