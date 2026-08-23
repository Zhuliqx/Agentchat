# 性能压测报告（benchmark.py）

> 相关文档：[README](../README.md) · [架构文档地图](ARCHITECTURE.md) · [项目2·自主任务Agent](AGENT_TASK.md)

> 目的：量化检索链路与完整对话的真实性能，为扩展决策（单 worker 何时不够、rerank 是否值得、GPU 必要性）提供数据依据。

## 1. 环境

| 项 | 说明 |
|----|------|
| 部署 | Docker Compose（Postgres + Milvus 容器）+ uvicorn 单 worker（:8000） |
| 压测机 | Windows 本机（与 Docker 同一台机器，Milvus 走容器网络 19530） |
| 模型 | embedding `BAAI/bge-small-zh-v1.5`（GPU cuda:0）、rerank `BAAI/bge-reranker-base`（GPU cuda:0，`rerank_candidate_k=6`）；`EMBEDDING_DEVICE=auto` 自动检测 |
| 知识库 | 3 篇文档，Milvus 231 向量块 |

## 2. 方法

- 脚本 `scripts/benchmark.py`：asyncio + httpx 并发，固定请求数（c1=60、c4/c8=200），warmup 3 次避模型首加载；
- 检索链路：`POST /api/rag/search`（与 RAG Agent 同路径：混合检索 → rerank → 去重合并）；
- 完整对话：`POST /api/chat/stream`（SSE），测 **TTFB（首 data 帧）** 与总耗时；
- rerank A/B：同一端口重启服务，进程内 `os.environ["RERANK_ENABLED"]="false"` 确保生效。

## 3. 结果

### 3.1 检索链路（QPS / 延迟 ms）

| 配置 | 并发 | QPS | p50 | p90 | p95 | p99 | max |
|------|------|-----|-----|-----|-----|-----|-----|
| rerank on | 1 | 12.1 | 82 | 86 | 87 | 89 | 89 |
| rerank on | 4 | 15.3 | 256 | 302 | 329 | 356 | 430 |
| rerank on | 8 | 16.1 | 487 | 552 | 598 | 647 | 686 |
| rerank off | 1 | 11.9 | 83 | 87 | 89 | 90 | 90 |
| rerank off | 4 | 14.2 | 288 | 307 | 321 | 366 | 391 |
| rerank off | 8 | 15.3 | 507 | 601 | 683 | 836 | 877 |

### 3.2 完整对话（SSE，并发 2 / 6 次）

| 指标 | p50 | p90 | p95 | 说明 |
|------|-----|-----|-----|------|
| **TTFB** | 18ms | 22ms | 22ms | SSE 首帧极快，流式体验好 |
| **总耗时** | 4986ms | 5862ms | 5863ms | 完整 RAG 问答（检索 + LLM 生成） |

## 4. 关键发现

### 4.1 rerank 不是检索延迟瓶颈（修正初始假设）

rerank on/off 同并发延迟差异 <5%（c1: 82 vs 83ms；c8: 487 vs 507ms）。
原因：`rerank_candidate_k=6` 的 base 模型推理成本低（单次 ~几 ms），
而检索主成本是 **embedding 推理 + Milvus 容器网络往返 + 单 worker 线程池排队**。

> 结论：在 CPU 单机、小知识库场景，rerank 的收益（命中率提升）远大于其成本，**保持默认开启**。

### 4.2 单 worker 吞吐饱和 ~16 QPS，并发延迟线性恶化

并发 1→8，QPS 12→16（几乎不涨），p50 82→487ms（6 倍）。
表明瓶颈是**单 worker 的串行/排队能力**（embedding/DB 调用在线程池排队），而非网络。

> 结论：这是「何时需要 `--workers`（多 worker）」的量化触发信号——
> 见 docs/DEPLOYMENT.md 阶段 2 演进。

### 4.3 对话链路 99% 时间在 LLM 生成

检索 83ms vs 完整对话 4986ms（p50）：LLM 生成占 ~98%。
SSE TTFB 仅 ~19ms（首事件即时），用户感知瓶颈在模型生成速度，不在服务端链路。

### 4.4 测量教训：`rerank_score` 是 Milvus 残留字段

`/api/rag/search` 返回的 hit 里 `rerank_score` 来自**摄入时写入 Milvus 的 metadata**
（连同 `H1/H2/chunk`），不是本次请求 rerank 执行的证据——判断 rerank 是否生效
必须看 `settings.rerank_enabled`，不能看响应字段。

## 5. 复现

```bash
# 检索（rerank on，服务默认）
python scripts/benchmark.py --endpoint search --label c1 --concurrency 1 --total 60
python scripts/benchmark.py --endpoint search --label c4 --concurrency 4 --total 200
python scripts/benchmark.py --endpoint search --label c8 --concurrency 8 --total 200

# rerank off：先在 backend/.env 设 RERANK_ENABLED=false，再重启服务（进程内读 config）
python run.py
python scripts/benchmark.py --endpoint search --label off-c8 --concurrency 8 --total 200

# 完整对话（需 LLM key）
python scripts/benchmark.py --endpoint chat --label chat --concurrency 2 --total 6
```

## 6. 结论

- 单机单 worker：检索峰值 ~16 QPS、p50 ~82ms（并发 1），满足个人/小团队；
- rerank 保持开启（收益 >> 成本）；
- 当检索并发 p95 超过可接受阈值或 QPS 需求 >16 时，进入 DEPLOYMENT.md 阶段 2
  （多 worker / 共享缓存落库）；embedding/rerank 已跑在 GPU，再压延迟的方向是 Milvus 本地化（减少容器网络往返）而非模型设备。