"""RAG 检索级回归评估：MRR + Hit@K（无需 LLM，可作 CI 回归基线）。

用法：
    python scripts/eval_rag.py                        # 内置案例（关键词判定）
    python scripts/eval_rag.py --dataset data/eval/ground_truth.json  # 用 GT 案例
    python scripts/eval_rag.py --compare A.json B.json

判定说明：
- 内置案例按"期望关键词"子串匹配（保持向后兼容）；
- GT 模式下优先按 expected_sources 判"来源命中"，其次退回关键词；
- 计算指标：Hit@1/3/5、MRR（首命中的倒数排名）；每条记录 rank 供排查。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import metrics, setup_utf8_stdio
from app.evaluation.dataset import load_ground_truth
from app.evaluation.judge_llm import build_ndcg_prompt, judge
from app.rag import hybrid
from app.rag.retriever import get_retriever

setup_utf8_stdio()

EVAL_DIR = Path(__file__).resolve().parent.parent / "data" / "eval"

# 内置回归案例：(问题, 期望关键词, top_k)
CASES: list[dict] = [
    {"question": "公司有多少名员工", "expected": ["员工", "120"], "top_k": 4},
    {"question": "公司的旗舰产品是什么", "expected": ["多 Agent", "平台"], "top_k": 4},
    {"question": "公司成立于哪一年", "expected": ["2020"], "top_k": 4},
    {"question": "知识库中有什么内容", "expected": ["公司", "产品", "员工"], "top_k": 4},
]


def _hit(mode: str, expected: list[str], source: str, text: str) -> bool:
    """判定单个检索结果是否命中期望标记。"""
    if not expected:
        return False
    if mode == "source":
        return any(s and s in source for s in expected)
    joined = f"{source} {text}".lower()
    return any(k.lower() in joined for k in expected)


def _eval_case(query: str, expected: list[str], mode: str, top_k: int) -> dict:
    hits = hybrid.search_hybrid(query, top_k=top_k)
    rank = next(
        (
            i
            for i, h in enumerate(hits, start=1)
            if _hit(mode, expected, str(h.get("source", "")), str(h.get("text", "")))
        ),
        None,
    )
    return {
        "query": query,
        "expected": expected,
        "mode": mode,
        "hits_count": len(hits),
        "rank": rank,
        "hit_at": {f"hit@{k}": bool(rank is not None and rank <= k) for k in (1, 3, 5)},
        "mrr_contrib": 1.0 / rank if rank else 0.0,
        "sources": [str(h.get("source", "")) for h in hits][:5],
        "first": str(hits[0].get("text", ""))[:60] if hits else "",
    }


# ---------------- NDCG 分级模式（--graded，需 LLM judge） ----------------

async def _eval_case_graded(query: str, hits: list[dict]) -> dict:
    """单条：检索结果由 judge 打 0/1/2 分级，计算 NDCG@1/3/5。"""
    docs = [
        {"text": str(h.get("text", "")), "source": str(h.get("source", ""))}
        for h in hits
    ]
    r = await judge(build_ndcg_prompt(query, docs))
    rel = metrics.safe_graded_list(r.get("relevance"), len(docs))
    return {
        "query": query,
        "hits_count": len(docs),
        "relevance": rel,
        "ndcg@1": round(metrics.ndcg_at_k(rel, 1), 4),
        "ndcg@3": round(metrics.ndcg_at_k(rel, 3), 4),
        "ndcg@5": round(metrics.ndcg_at_k(rel, 5), 4),
    }


def _collect_docs_retriever(cases: list[dict], top_k: int) -> list[list[dict]]:
    """用生产同路径 retriever（混合检索 + rerank + 去重合并）获取排序结果。

    关键：--graded 若直接用 hybrid.search_hybrid，会测"原始混合排序"而非
    真实系统输出（q05 实证：核心块在 hybrid 排 #2，rerank 后升到 #1），
    导致 NDCG 低估排序质量。故与 eval_quality 一致，走 retriever。
    """
    retriever = get_retriever(user_id="default")
    retriever.top_k = top_k
    return [
        [
            {
                "text": d.page_content,
                "source": (d.metadata or {}).get("source", ""),
            }
            for d in retriever.invoke(c["question"])
        ]
        for c in cases
    ]


def run_eval_graded(cases: list[dict], top_k: int, concurrency: int = 4) -> dict:
    """NDCG 分级评估：retriever(生产同路径) → LLM 分级 → 汇总 NDCG@1/3/5。"""
    print(f"RAG graded eval（NDCG，top_k={top_k}，{len(cases)} 案例，LLM judge）\n")
    hits_list = _collect_docs_retriever(cases, top_k)

    async def _run_all() -> list[dict]:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def guarded(case: dict, hits: list[dict]) -> dict:
            async with sem:
                return await _eval_case_graded(case["question"], hits)

        return await asyncio.gather(
            *(guarded(c, h) for c, h in zip(cases, hits_list))
        )

    results = asyncio.run(_run_all())
    for r in results:
        print(
            f"[{r['query'][:28]:<28}] rel={r['relevance']} "
            f"NDCG@1={r['ndcg@1']:.3f} @3={r['ndcg@3']:.3f} @5={r['ndcg@5']:.3f}"
        )
    total = len(results)
    summary = {
        f"ndcg@{k}": round(sum(r[f"ndcg@{k}"] for r in results) / total, 4)
        if total
        else None
        for k in (1, 3, 5)
    }
    print(
        f"\nNDCG@1={summary['ndcg@1']:.3f}  NDCG@3={summary['ndcg@3']:.3f}  "
        f"NDCG@5={summary['ndcg@5']:.3f}"
    )
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": "graded-ndcg",
        "top_k": top_k,
        "cases": results,
        "summary": summary,
    }


def _summarize(results: list[dict]) -> dict:
    total = len(results)
    mrr = sum(r["mrr_contrib"] for r in results) / total if total else 0.0
    summary: dict[str, float | int | None] = {"total": total, "mrr": round(mrr, 4)}
    for k in ("hit@1", "hit@3", "hit@5"):
        vals = [bool(r["hit_at"].get(k, False)) for r in results]
        summary[k] = round(sum(vals) / total, 4) if total else None
    return summary


def _hit_at_label(r: dict) -> str:
    parts = [f"{k}:{'Y' if v else 'N'}" for k, v in sorted(r["hit_at"].items())]
    return " ".join(parts)


def run_eval(cases: list[dict], mode: str, top_k: int) -> dict:
    print(f"RAG retrieval eval（mode={mode}, top_k={top_k}, {len(cases)} 案例）\n")
    results = [_eval_case(c["question"], c.get("expected", []), mode, c.get("top_k") or top_k) for c in cases]
    for r in results:
        mark = "OK" if r["rank"] is not None else "X"
        print(
            f"[{mark}] {r['query'][:28]:<28} rank={r['rank']} "
            f"hits={r['hits_count']} {_hit_at_label(r)}"
        )
        if r["first"]:
            print(f"     first: {r['first']}")
    summary = _summarize(results)
    print(
        f"\nMRR={summary['mrr']:.3f}  "
        f"Hit@1={summary['hit@1']:.3f}  Hit@3={summary['hit@3']:.3f}  "
        f"Hit@5={summary['hit@5']:.3f}"
    )
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "top_k": top_k,
        "cases": results,
        "summary": summary,
    }


def save_result(payload: dict) -> Path:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_DIR / f"rag_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {path}")
    return path


def compare(path_a: Path, path_b: Path) -> int:
    a = json.loads(path_a.read_text(encoding="utf-8-sig"))
    b = json.loads(path_b.read_text(encoding="utf-8-sig"))
    sa, sb = a["summary"], b["summary"]
    print(f"对比 {path_a.name}  →  {path_b.name}\n")
    print(f"{'问题':<30}{'MRR(A)':<10}{'MRR(B)':<10}变化")
    # 按 query 对齐（两份 JSON 的 case 顺序/数量可能不同，不能按位置 zip）
    mb = {r.get("query", ""): r for r in b.get("cases", [])}
    for ra in a.get("cases", []):
        rb = mb.get(ra.get("query", ""))
        if rb is None:
            continue
        mrr_a = ra.get("mrr_contrib", 0.0)
        mrr_b = rb.get("mrr_contrib", 0.0)
        delta = "→MRR↑" if mrr_b > mrr_a else ("→MRR↓" if mrr_b < mrr_a else "—")
        print(f"{ra.get('query', '')[:30]:<30}{mrr_a:<10.2f}{mrr_b:<10.2f}{delta}")
    print(
        f"\nMRR: {sa.get('mrr'):.3f} → {sb.get('mrr'):.3f} "
        f"({sb.get('mrr', 0) - sa.get('mrr', 0):+.3f})"
    )
    return 0 if sb.get("mrr", 0) >= sa.get("mrr", 0) else 1


def load_cases(dataset_path: str | None) -> tuple[list[dict], str]:
    """案例来源：内置(关键词模式) 或 GT 文件(source 模式优先，退回关键词)。"""
    if not dataset_path:
        return list(CASES), "keywords"
    try:
        gts = load_ground_truth(dataset_path)
    except Exception as exc:
        print(f"⚠ 读取 GT 失败: {exc}；退回内置案例")
        return list(CASES), "keywords"
    gt_cases = []
    for c in gts:
        # 仅当 GT 提供了 expected_sources 才判"来源命中"；
        # 无来源的 case 无判定依据，跳过（避免退回"问题分词"导致误报 miss）
        if not c.expected_sources:
            continue
        gt_cases.append(
            {
                "question": c.question,
                "expected": c.expected_sources,
                "top_k": 4,
            }
        )
    if not gt_cases:
        print("GT 均未提供 expected_sources，退回内置案例")
        return list(CASES), "keywords"
    return gt_cases, "source"


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索回归评估（MRR/Hit@K，可选 NDCG 分级）")
    parser.add_argument("--dataset", default=None, help="GT json 路径（默认内置案例）")
    parser.add_argument("--top-k", type=int, default=4, help="检索 top_k")
    parser.add_argument("--graded", action="store_true", help="NDCG 分级模式（LLM judge 打 0/1/2）")
    parser.add_argument("--concurrency", type=int, default=4, help="graded 模式 LLM 并发数")
    parser.add_argument("--rerank-max-length", type=int, default=None, help="覆盖 RERANK_MAX_LENGTH（A/B）")
    parser.add_argument(
        "--compare", nargs=2, metavar=("A", "B"), help="对比两次评估结果 JSON"
    )
    args = parser.parse_args()

    if args.compare:
        raise SystemExit(compare(Path(args.compare[0]), Path(args.compare[1])))

    if args.rerank_max_length is not None:
        from app.config import settings

        settings.rerank_max_length = args.rerank_max_length

    cases, mode = load_cases(args.dataset)
    if args.graded:
        payload = run_eval_graded(cases, top_k=args.top_k, concurrency=args.concurrency)
    else:
        payload = run_eval(cases, mode=mode, top_k=args.top_k)
    save_result(payload)


if __name__ == "__main__":
    main()