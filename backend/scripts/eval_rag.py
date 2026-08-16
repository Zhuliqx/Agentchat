"""RAG 检索评估：对固定问题集跑混合检索，统计 top-k 命中率（无需 LLM）。

用法:
    python scripts/eval_rag.py

需要 Postgres + Milvus 运行中（先摄入文档）。命中判定为：top-k 命中文本或
source 中包含任一期望关键词（大小写不敏感，中文直接子串匹配）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 问题集：(问题, 期望命中关键词列表, top_k)
CASES = [
    ("公司有多少名员工", ["员工", "120"], 4),
    ("公司的旗舰产品是什么", ["多 Agent", "平台"], 4),
    ("公司成立于哪一年", ["2020"], 4),
    ("知识库中有什么内容", ["公司", "产品", "员工"], 4),
]


def main() -> None:
    from app.rag import hybrid

    print("RAG retrieval eval (fixed cases, top-k hit rate)\n")
    total_hit = 0
    total_case = len(CASES)
    for query, keywords, top_k in CASES:
        hits = hybrid.search_hybrid(query, top_k=top_k)
        joined = " ".join(
            str(h.get("text", "")) + " " + str(h.get("source", "")) for h in hits
        ).lower()
        hit = any(k.lower() in joined for k in keywords)
        total_hit += int(hit)
        mark = "[OK]" if hit else "[X]"
        print(f"{mark} [{query}] top{top_k} expected={keywords} hits={len(hits)}")
        if hits:
            print(f"    first: {str(hits[0].get('text', ''))[:60]}")
    print(f"\n命中率: {total_hit}/{total_case}")
    sys.exit(0 if total_hit == total_case else 1)


if __name__ == "__main__":
    main()
