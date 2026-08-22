"""Embedding 模型选型对比：同一 GT + 相同块文本，进程内暴力检索，对比来源级命中。

用法：
    python scripts/eval_embedding.py                          # 默认 bge-small vs bge-base
    python scripts/eval_embedding.py --models "BAAI/bge-small-zh-v1.5,BAAI/bge-base-zh-v1.5,moka-ai/m3e-large"
    python scripts/eval_embedding.py --max-cases 10

设计：
- 块文本从 Postgres documents 表读取（不碰 Milvus，规避 embedding_dim 维度校验）；
- 进程内暴力余弦检索（≈ HNSW 上界，公平对比 embedding 本身质量）；
- 判定：来源级 Hit@1/3 + MRR（与 eval_rag source 模式一致）；
- 模型需已在本机 HF 缓存（离线 local_files_only）；未下载的可设 HF_ENDPOINT=https://hf-mirror.com 预下载。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sqlalchemy import select

from app.config import settings
from app.db.postgres import SessionLocal
from app.db.models import Document
from app.evaluation import dataset, setup_utf8_stdio

setup_utf8_stdio()

EVAL_DIR = Path(__file__).resolve().parent.parent / "data" / "eval"
DEFAULT_GT = EVAL_DIR / "ground_truth.json"
DEFAULT_MODELS = "BAAI/bge-small-zh-v1.5,BAAI/bge-base-zh-v1.5"
TOP_K = 4


# ---------------- 数据加载 ----------------

def _load_chunks(user_id: str = "default") -> list[dict]:
    """从 Postgres 读全部块（不碰 Milvus）。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(Document.id, Document.text, Document.source, Document.chunk_index)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.asc())
        ).all()
    return [{"id": r[0], "text": r[1], "source": r[2], "chunk_index": r[3]} for r in rows]


# ---------------- 检索与判定 ----------------

def _eval_model(model_name: str, chunks: list[dict], queries: list[str], expected: list[list[str]], pooling: str = "cls") -> dict:
    """统一用 transformers 加载 + CLS pooling + L2 归一化（bge 系列）。

    不用 sentence-transformers：旧版 bge 模型（如 bge-base-zh-v1.5）在 ST 4.x 下
    因缺 1_Pooling/config.json 加载异常（输出无区分度）；transformers 直接加载正常。
    """
    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    print(f"\n模型: {model_name} ...")
    tok = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
    mdl = AutoModel.from_pretrained(model_name, local_files_only=True)
    device = settings.resolved_embedding_device()
    if device == "cuda" and torch.cuda.is_available():
        mdl = mdl.to("cuda")

    def _encode(texts: list[str], mode: str) -> np.ndarray:
        enc = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
        if device == "cuda":
            enc = {k: v.to("cuda") for k, v in enc.items()}
        with torch.no_grad():
            out = mdl(**enc)
        h = out.last_hidden_state
        if mode == "mean":
            mask = enc["attention_mask"].unsqueeze(-1).float()
            v = (h * mask).sum(1) / mask.sum(1).clamp(min=1)
        else:
            v = h[:, 0]  # CLS pooling
        return F.normalize(v, p=2, dim=1).cpu().numpy()

    chunk_vecs = _encode([c["text"] for c in chunks], pooling)
    q_vecs = _encode(queries, pooling)
    chunk_mat = np.asarray(chunk_vecs)

    hit1 = hit3 = mrr = 0
    for qi, q in enumerate(q_vecs):
        scores = chunk_mat @ np.asarray(q)
        top = np.argsort(-scores)[:TOP_K]
        sources = [chunks[i]["source"] for i in top]
        exp = set(expected[qi])
        rank = next((k for k, s in enumerate(sources, start=1) if s in exp), None)
        if rank is not None:
            mrr += 1.0 / rank
            if rank == 1:
                hit1 += 1
            if rank <= 3:
                hit3 += 1
    n = len(queries)
    return {
        "model": model_name,
        "dim": chunk_mat.shape[1],
        "hit@1": round(hit1 / n, 4),
        "hit@3": round(hit3 / n, 4),
        "mrr": round(mrr / n, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Embedding 选型对比（来源级命中）")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="逗号分隔模型列表")
    parser.add_argument("--dataset", default=None, help="GT json（默认 ground_truth.json）")
    parser.add_argument("--max-cases", type=int, default=0, help="只评估前 N 条")
    parser.add_argument("--pooling", default="cls", choices=["cls", "mean"], help="池化方式（bge-base 建议 mean）")
    args = parser.parse_args()

    gt_path = Path(args.dataset or DEFAULT_GT)
    cases = dataset.load_ground_truth(gt_path)
    if args.max_cases:
        cases = cases[: args.max_cases]
    chunks = _load_chunks()
    queries = [c.question for c in cases]
    expected = [c.expected_sources for c in cases]
    print(f"Embedding 选型：{len(cases)} 条 GT × {len(chunks)} 块（数据源: {gt_path.name}）")

    results = []
    for m in [x.strip() for x in args.models.split(",") if x.strip()]:
        results.append(_eval_model(m, chunks, queries, expected, pooling=args.pooling))

    print("\n===== 对比表 =====")
    print(f"{'模型':<40}{'维度':>6}{'Hit@1':>8}{'Hit@3':>8}{'MRR':>8}")
    for r in results:
        print(f"{r['model']:<40}{r['dim']:>6}{r['hit@1']:>8.4f}{r['hit@3']:>8.4f}{r['mrr']:>8.4f}")

    out = EVAL_DIR / f"embedding_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(__import__("json").dumps(
        {"timestamp": datetime.now().isoformat(timespec="seconds"),
         "gt": str(gt_path), "chunks": len(chunks), "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()