"""混合检索超参 sweep + RAG 三档检索级复测（无 LLM，可复现）。

用法：
    python scripts/eval_rag_sweep.py                       # 默认：书面集超参 sweep + 三档（口语/难例）检索级
    python scripts/eval_rag_sweep.py --dataset <gt.json>   # 指定数据集
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

# 复用同级 eval_rag 的检索级评估函数（脚本直接运行时 scripts/ 在 sys.path）
from eval_rag import _collect_docs_retriever, _eval_case, _summarize, load_cases

EVAL = Path("data/eval")


def bench(dataset: str, overrides: dict, label: str) -> dict:
    """在给定 GT 上跑检索级命中评估，覆盖若干配置，返回聚合指标。"""
    for k, v in overrides.items():
        setattr(settings, k, v)
    cases, mode = load_cases(str(EVAL / dataset))
    hits = _collect_docs_retriever(cases, 4)
    results = [
        _eval_case(
            c["question"], c["expected"], mode, h, c.get("top_k") or 4, c.get("expected_images")
        )
        for c, h in zip(cases, hits)
    ]
    s = _summarize(results)
    print(f"{label:26s} MRR={s['mrr']:.3f}  Hit@1={s['hit@1']:.3f}  Hit@3={s['hit@3']:.3f}")
    return {"label": label, "mrr": s["mrr"], "hit@1": s["hit@1"], "hit@3": s["hit@3"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=None, help="GT json（覆盖默认书面集）")
    ap.add_argument("--sweep", action="store_true", help="只跑 P3-7 超参 sweep")
    args = ap.parse_args()

    res: list[dict] = []
    # ---------------- 混合检索超参 sweep（默认书面集） ----------------
    W = args.dataset or "ground_truth.json"
    print(f"== 混合检索超参 sweep  · {W} ==")
    for rrf, cand in [(60, 20), (40, 20), (80, 20), (60, 10), (60, 30)]:
        res.append(bench(W, {"rrf_k": rrf, "hybrid_candidate_k": cand}, f"rrf{rrf}/cand{cand}"))

    if args.sweep:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return

    # ---------------- RAG 三档检索级复测（口语/难例集） ----------------
    print("\n== RAG 三档检索级复测（口语/难例） ==")
    for ds in ["ground_truth_spoken.json", "ground_truth_hard.json"]:
        print(f"-- {ds} --")
        base = {"rrf_k": 60, "hybrid_candidate_k": 20}
        res.append(bench(ds, dict(base), "off"))
        res.append(bench(ds, dict(base, adaptive_retrieval=True), "adaptive"))
        res.append(bench(ds, dict(base, intent_routing=True), "intent"))
        res.append(bench(ds, dict(base, dedup_near_duplicate=True, rag_max_total_chars=3000), "dedup+预算"))

    print("\n==== 汇总 ====")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
