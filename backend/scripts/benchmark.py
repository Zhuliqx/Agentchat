"""性能压测脚本：检索链路（/api/rag/search）与完整对话（/api/chat/stream）。

用法：
    # 检索链路（无 LLM 成本，可高并发）
    python scripts/benchmark.py --endpoint search --label "rerank-on-c4" \
        --concurrency 4 --total 200 --query "公司成立于哪一年？"

    # 完整对话（SSE，首 token 延迟 TTFB + 总耗时；需 LLM key，用少并发/少量）
    python scripts/benchmark.py --endpoint chat --label "chat-c2" \
        --concurrency 2 --total 6 --query "公司成立于哪一年？"

rerank 开关是服务端配置（RERANK_ENABLED env），A/B 对比时用不同 env 起服务再各跑一轮，
用 --label 区分。结果只打印到 stdout，便于整理进 README 量化成果表。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

SEARCH_QUERY = "公司成立于哪一年？"
CHAT_BODY = {
    "message": SEARCH_QUERY,
    "use_rag": True,
    "use_search": False,
    "use_memory": False,
}


def _percentile(sorted_vals: list[float], p: float) -> float:
    """线性插值百分位。"""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _print_stats(label, concurrency, total, elapsed, lats, errors, prefix="延迟") -> None:
    lats.sort()
    n = len(lats)
    print(f"[{label}] 并发 {concurrency} / 请求 {total} / 耗时 {elapsed:.1f}s")
    print(f"  QPS={total / elapsed:.1f}  错误={errors}")
    if n:
        print(
            f"  {prefix}(ms): min={lats[0]:.1f} p50={_percentile(lats, 50):.1f} "
            f"p90={_percentile(lats, 90):.1f} p95={_percentile(lats, 95):.1f} "
            f"p99={_percentile(lats, 99):.1f} max={lats[-1]:.1f} "
            f"mean={sum(lats) / n:.1f}"
        )


async def _search_once(client: httpx.AsyncClient, base: str, query: str) -> None:
    resp = await client.post(
        base + "/api/rag/search", params={"query": query, "top_k": 4}
    )
    resp.raise_for_status()


async def _chat_once(client: httpx.AsyncClient, base: str, query: str) -> tuple[float, float]:
    """完整对话：返回 (首 token TTFB ms, 总耗时 ms)。"""
    t0 = time.perf_counter()
    ttfb = None
    async with client.stream(
        "POST", base + "/api/chat/stream", json={**CHAT_BODY, "message": query}
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data:") and ttfb is None:
                ttfb = (time.perf_counter() - t0) * 1000
    return ttfb or 0.0, (time.perf_counter() - t0) * 1000


async def _bench(client, endpoint, base, query, concurrency, total, warmup, label) -> None:
    # warmup：加载 rerank/embedding 模型、建 BM25 索引，避免污染测量
    for _ in range(warmup):
        try:
            if endpoint == "search":
                await _search_once(client, base, query)
            else:
                await _chat_once(client, base, query)
        except Exception:
            pass

    sem = asyncio.Semaphore(concurrency)
    lats = []
    ttfb_list = []
    errors = 0
    start = time.perf_counter()

    async def one() -> None:
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                if endpoint == "search":
                    await _search_once(client, base, query)
                    lats.append((time.perf_counter() - t0) * 1000)
                else:
                    ttfb, total_ms = await _chat_once(client, base, query)
                    ttfb_list.append(ttfb)
                    lats.append(total_ms)
            except Exception:
                errors += 1

    await asyncio.gather(*(one() for _ in range(total)))
    elapsed = time.perf_counter() - start
    if endpoint == "search":
        _print_stats(label, concurrency, total, elapsed, lats, errors)
    else:
        print(f"[{label}] 完整对话（SSE） 并发 {concurrency} / 请求 {total} / 耗时 {elapsed:.1f}s")
        print(f"  错误={errors}")
        _print_stats(label, concurrency, total, elapsed, lats, errors, prefix="总耗时")
        if ttfb_list:
            _print_stats(label, concurrency, total, elapsed, ttfb_list, errors, prefix="TTFB")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG/对话性能压测")
    parser.add_argument("--endpoint", choices=["search", "chat"], default="search")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--query", default=SEARCH_QUERY)
    parser.add_argument("--label", default="bench")
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()

    async def run() -> None:
        timeout = httpx.Timeout(120.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            await _bench(
                client,
                args.endpoint,
                args.base,
                args.query,
                args.concurrency,
                args.total,
                args.warmup,
                args.label,
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
