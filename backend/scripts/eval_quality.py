"""端到端 RAG 质量评估（自研 LLM-judge 四指标，对齐 RAGAS 口径）。

用法：
    python scripts/eval_quality.py                          # 默认配置跑全部指标
    python scripts/eval_quality.py --max-cases 5            # 只跑前 5 条（试成本）
    python scripts/eval_quality.py --skip-generation        # 仅检索层两指标
    # A/B：换参数各跑一次并保存，再对比
    python scripts/eval_quality.py --no-rerank --out eval_ab_no_rerank.json
    python scripts/eval_quality.py --top-k 6   --out eval_ab_topk6.json
    python scripts/eval_quality.py --compare eval_ab_no_rerank.json eval_ab_topk6.json

说明：
- 检索与生产完全同路径（混合检索 + rerank + 上下文压缩），见 app.rag.retriever。
- judge 固定 DeepSeek、temperature=0；生成器为对话主模型。
- Ground truth 默认读取 data/eval/ground_truth.json（--dataset 覆盖）。
- 指标定义见 app/evaluation/metrics.py 与 docs/EVALUATION.md。
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
from app.evaluation import dataset, metrics, setup_utf8_stdio
from app.evaluation.dataset import Case
from app.evaluation.judge_llm import (
    build_faithfulness_prompt,
    build_generation_prompt,
    build_precision_prompt,
    build_recall_prompt,
    build_relevancy_prompt,
    get_eval_generator,
    judge,
)
from app.rag.retriever import get_retriever

setup_utf8_stdio()

EVAL_DIR = Path(__file__).resolve().parent.parent / "data" / "eval"
DEFAULT_GT = EVAL_DIR / "ground_truth.json"
CONCURRENCY = 4

# 评估用知识库用户（环境变量 RAG_EVAL_USER 覆盖；默认 "default"，与既有行为一致）
import os as _os
_USER_ID = _os.environ.get("RAG_EVAL_USER", "default")


# ---------------- 检索（与生产同路径，线程池执行） ----------------

def _retrieve_sync(question: str) -> list[dict]:
    retriever = get_retriever(user_id=_USER_ID)
    docs = retriever.invoke(question)
    return [
        {
            "text": d.page_content,
            "source": (d.metadata or {}).get("source", ""),
            "metadata": dict(d.metadata or {}),
        }
        for d in docs
    ]


async def _generate_answer(question: str, docs: list[dict]) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_eval_generator()
    sys, user = build_generation_prompt(question, docs)
    resp = await llm.ainvoke([SystemMessage(content=sys), HumanMessage(content=user)])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    return (text or "").strip()


# ---------------- 单条 case 评估 ----------------

async def eval_case(case: Case, skip_generation: bool = False) -> dict:
    """对一条 GT 计算四指标；任何子步骤失败不中断整批。"""
    record: dict[str, Any] = {"id": case.id, "question": case.question}
    try:
        docs = await asyncio.to_thread(_retrieve_sync, case.question)
        record["docs_count"] = len(docs)
        record["sources"] = list(dict.fromkeys(d.get("source", "") for d in docs))

        # 检索层指标
        if (r := await judge(build_precision_prompt(case.question, docs))).get("relevant") is not None:
            rel = metrics.safe_bool_list(r.get("relevant"), len(docs))
        else:
            rel = [False] * len(docs)
        record["metrics.context_precision"] = round(metrics.context_precision(rel), 4)

        if case.answer:
            rr = await judge(build_recall_prompt(case.question, case.answer, docs))
            key_points = rr.get("key_points") if isinstance(rr.get("key_points"), list) else []
            covered = metrics.safe_bool_list(rr.get("covered"), len(key_points)) if key_points else []
            record["recall_key_points"] = len(key_points)
            record["metrics.context_recall"] = round(metrics.context_recall(covered), 4)
        else:
            record["metrics.context_recall"] = None

        # 生成层指标（可跳过用 --skip-generation）
        if skip_generation:
            record["skip_generation"] = True
            return record

        answer = await _generate_answer(case.question, docs)
        record["answer"] = answer
        if not answer:
            record["metrics.faithfulness"] = 0.0
            record["metrics.answer_relevancy"] = 0.0
            return record

        f, rl = await asyncio.gather(
            judge(build_faithfulness_prompt(answer, docs)),
            judge(build_relevancy_prompt(case.question, answer)),
        )
        sentences = f.get("sentences") if isinstance(f.get("sentences"), list) else []
        supported = (
            metrics.safe_bool_list(f.get("supported"), len(sentences)) if sentences else []
        )
        record["metrics.faithfulness"] = round(metrics.faithfulness(supported), 4)
        record["metrics.answer_relevancy"] = round(metrics.answer_relevancy(rl.get("score")), 4)
        return record

    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record


# ---------------- 汇总 / 报告 / 对比 ----------------

_METRIC_KEYS = [
    "metrics.context_precision",
    "metrics.context_recall",
    "metrics.faithfulness",
    "metrics.answer_relevancy",
]


def _summarize(records: list[dict]) -> dict[str, Any]:
    summary: dict[str, Any] = {"cases_total": len(records), "cases_ok": 0}
    cols: dict[str, list[float]] = {k: [] for k in _METRIC_KEYS}
    for r in records:
        if r.get("error"):
            continue
        summary["cases_ok"] += 1
        for k in _METRIC_KEYS:
            v = r.get(k)
            if isinstance(v, (int, float)):
                cols[k].append(float(v))
    for k, vals in cols.items():
        summary[k] = round(metrics.macro_average(vals), 4) if vals else None
    return summary


def _md_report(meta: dict[str, Any], records: list[dict], summary: dict[str, Any]) -> str:
    lines = [
        f"# RAG 质量评估报告 — {meta.get('dataset', 'run')}",
        "",
        f"- 生成时间: `{meta.get('timestamp')}`",
        f"- 配置: `{json.dumps(meta.get('params', {}), ensure_ascii=False)}`",
        f"- 规模: {summary.get('cases_ok')}/{summary.get('cases_total', len(records))} 条成功",
        "",
        "## 汇总指标（0-1，macro 平均）",
        "",
        "| 指标 | 平均分 | 说明 |",
        "|------|--------|------|",
        "| context_precision | {:.3f} | 检索块相关率（位置加权，靠前加分） |".format(
            summary.get("metrics.context_precision") or 0
        ),
        "| context_recall | {:.3f} | GT 关键信息被检索块覆盖的比例 |".format(
            summary.get("metrics.context_recall") or 0
        ),
        "| faithfulness | {:.3f} | 答案句子可支撑比例（幻觉越低越高） |".format(
            summary.get("metrics.faithfulness") or 0
        ),
        "| answer_relevancy | {:.3f} | 答案切题度 |".format(
            summary.get("metrics.answer_relevancy") or 0
        ),
        "",
        "## 逐条明细",
        "",
        "| id | 问题 | 检索块 | CP | CR | Faith | Rel |",
        "|----|------|--------|----|----|-------|-----|",
    ]
    for r in records:
        if r.get("error"):
            lines.append(
                f"| {r['id']} | ⚠️ {r.get('error', '')[:48].replace('|', '/')} | - | - | - | - | - |"
            )
            continue
        lines.append(
            f"| {r['id']} | {r.get('question', '')[:24]} | {r.get('docs_count', 0)} | "
            f"{r.get('metrics.context_precision', '-')} | {r.get('metrics.context_recall', '-')} | "
            f"{r.get('metrics.faithfulness', '-')} | {r.get('metrics.answer_relevancy', '-')} |"
        )
    lines.append("")
    lines.append(
        "> 评估途径：`eval_quality.py`（LLM-as-judge，DeepSeek temperature=0）。"
        "绝对值受 judge 同源偏好影响，A/B 相对变化更有参考价值。"
    )
    lines.append("")
    return "\n".join(lines)


async def _run_all(
    cases: list[Case],
    *,
    skip_generation: bool,
    concurrency: int,
) -> list[dict]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def guarded(case: Case) -> dict:
        async with sem:
            return await eval_case(case, skip_generation=skip_generation)

    return await asyncio.gather(*(guarded(c) for c in cases))


# ---------------- CLI ----------------

def _apply_overrides(args: argparse.Namespace) -> list[str]:
    """把 CLI 参数覆盖到 settings；返回变更描述。"""
    changed: list[str] = []
    if args.top_k is not None:
        settings.rag_top_k = args.top_k
        changed.append(f"rag_top_k={args.top_k}")
    if args.threshold is not None:
        settings.rag_score_threshold = args.threshold
        changed.append(f"rag_score_threshold={args.threshold}")
    if args.no_rerank:
        settings.rerank_enabled = False
        changed.append("rerank_enabled=False")
    if args.no_hybrid:
        settings.hybrid_search = False
        changed.append("hybrid_search=False")
    if args.max_per_doc is not None:
        settings.rag_max_per_doc = args.max_per_doc
        changed.append(f"rag_max_per_doc={args.max_per_doc}")
    if args.rewrite is not None:
        settings.query_rewrite_enabled = args.rewrite != "none"
        if args.rewrite != "none":
            settings.query_rewrite_mode = args.rewrite
        changed.append(f"query_rewrite_mode={args.rewrite}")
    return changed


def _overrides() -> dict[str, Any]:
    """记录本次评估所用检索参数（写入报告便于复现）。"""
    return {
        "rag_top_k": settings.rag_top_k,
        "rag_score_threshold": settings.rag_score_threshold,
        "hybrid_search": settings.hybrid_search,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_candidate_k": settings.rerank_candidate_k,
        "rag_max_per_doc": settings.rag_max_per_doc,
        "rag_max_chunk_chars": settings.rag_max_chunk_chars,
        "query_rewrite_enabled": settings.query_rewrite_enabled,
        "query_rewrite_mode": settings.query_rewrite_mode,
    }


def _terminal_output(records: list[dict], summary: dict[str, Any]) -> None:
    print(f"\n通过: {summary['cases_ok']}/{summary['cases_total']}\n")
    for r in records:
        if r.get("error"):
            print(f"  [{r['id']}] [X] {r.get('question', '')[:30]} -> {r['error']}")
            continue
        print(
            f"  [{r['id']}] CP={(r.get('metrics.context_precision') or 0.0):.3f} "
            f"CR={(r.get('metrics.context_recall') or 0.0):.3f} "
            f"F={(r.get('metrics.faithfulness') or 0.0):.3f} "
            f"R={(r.get('metrics.answer_relevancy') or 0.0):.3f} "
            f"| 检索块={r.get('docs_count', 0)}"
        )
    print("\n汇总 (macro):")
    for k in _METRIC_KEYS:
        print(f"  {k}: {summary.get(k)}")


def _run(args: argparse.Namespace) -> int:
    gt_path = Path(args.dataset or DEFAULT_GT)
    cases = dataset.load_ground_truth(gt_path)
    if args.max_cases:
        cases = cases[: args.max_cases]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c.id in wanted]
        if not cases:
            print(f"没有匹配的案例 id: {args.only}")
            return 1
    if not cases:
        print("ground truth 无案例，请先编写 data/eval/ground_truth.json")
        return 1

    changed = _apply_overrides(args)
    print(
        f"评估 {len(cases)} 条（数据源: {gt_path}）"
        f"；参数覆盖: {', '.join(changed) or '无（默认配置）'}"
    )

    records = asyncio.run(
        _run_all(cases, skip_generation=args.skip_generation, concurrency=args.concurrency)
    )
    summary = _summarize(records)
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "params": _overrides(),
        "dataset": str(gt_path),
        "skip_generation": bool(args.skip_generation),
    }
    _terminal_output(records, summary)

    if args.out:
        out = Path(args.out)
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
        if args.report:
            report_path = out.with_suffix(".md")
            report_path.write_text(_md_report(meta, records, summary), encoding="utf-8")
            print(f"报告已保存: {report_path}")
    return 0


def _compare(a: dict, b: dict) -> int:
    print(f"对比 {a['meta']['dataset']}  →  {b['meta']['dataset']}")
    print(f"  A 参数: {json.dumps(a['meta']['params'], ensure_ascii=False)}")
    print(f"  B 参数: {json.dumps(b['meta']['params'], ensure_ascii=False)}\n")
    print(f"{'指标':<26}{'A':>8}{'B':>8}{'Δ':>10}")
    for k in _METRIC_KEYS:
        va, vb = a["summary"].get(k), b["summary"].get(k)
        if va is None or vb is None:
            print(f"{k:<26}{'-':>8}{'-':>8}{'-':>10}")
            continue
        print(f"{k:<26}{va:>8.3f}{vb:>8.3f}{vb - va:+10.3f}")

    # 逐 case 对齐（按 id）：faithfulness 胜/负/平 + 变化最大者（洞察瓶颈在哪些案例）
    mb = {r.get("id"): r for r in b.get("cases", [])}
    deltas: list[tuple[str, float, float]] = []
    for ra in a.get("cases", []):
        rb = mb.get(ra.get("id"))
        if rb is None:
            continue
        fa, fb = ra.get("metrics.faithfulness"), rb.get("metrics.faithfulness")
        if isinstance(fa, (int, float)) and isinstance(fb, (int, float)):
            deltas.append((ra.get("id", ""), float(fb) - float(fa), float(fb)))
    if deltas:
        wins = sum(1 for _, d, _ in deltas if d > 0.01)
        ties = sum(1 for _, d, _ in deltas if abs(d) <= 0.01)
        losses = sum(1 for _, d, _ in deltas if d < -0.01)
        print(f"\nfaithfulness 逐条: 胜 {wins} / 平 {ties} / 负 {losses}")
        for label, key in (("提升最多", False), ("下降最多", True)):
            ranked = sorted(deltas, key=lambda x: x[1], reverse=not key)
            top = ", ".join(f"{i}:{d:+.2f}" for i, d, _ in ranked[:5] if abs(d) > 0.01)
            if top:
                print(f"  {label}: {top}")
    print(
        "\n结论方向：相对变化可信；绝对值受 LLM-judge 同源偏好影响，"
        "建议同时看逐 case 明细文件。"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 质量评估（四指标 LLM-judge，DeepSeek）")
    parser.add_argument("--dataset", default=None, help="GT json（默认 data/eval/ground_truth.json）")
    parser.add_argument("--max-cases", type=int, default=0, help="只评估前 N 条（试运行）")
    parser.add_argument("--only", default=None, help="只评估指定案例 id，逗号分隔（如 q28,q31）")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY, help="并发评审数")
    parser.add_argument("--skip-generation", action="store_true", help="跳过生成层（只算检索两指标）")
    parser.add_argument("--top-k", type=int, default=None, help="覆盖 RAG_TOP_K")
    parser.add_argument("--threshold", type=float, default=None, help="覆盖 RAG_SCORE_THRESHOLD")
    parser.add_argument("--no-rerank", action="store_true", help="关闭 rerank（A/B）")
    parser.add_argument("--no-hybrid", action="store_true", help="关闭混合检索（A/B）")
    parser.add_argument("--max-per-doc", type=int, default=None, help="覆盖 RAG_MAX_PER_DOC（A/B）")
    parser.add_argument(
        "--rewrite",
        default=None,
        choices=["none", "rule", "llm"],
        help="启用并覆盖 QUERY_REWRITE_MODE（A/B）；none=关闭改写",
    )
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--report", action="store_true", help="同时生成 Markdown 报告")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="对比两份结果 JSON")
    args = parser.parse_args()

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        raise SystemExit(_compare(a, b))
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()