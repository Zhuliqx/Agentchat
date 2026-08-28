# 文档地图与基线

> 本仓库两个项目（**项目 1 · Agentchat** 主应用、**项目 2 · task-agent** 独立包）的全部文档索引与**唯一基线数字**。
> 最后校验：2026-08-29（防漂移检查见 `backend/scripts/check_docs_stale.py`）

## 文档类型速查

| 类型 | 文档 | 说明 |
|------|------|------|
| 入口 | [README](../README.md)（仓库根） | 简历速览 + 量化成果 + 目录结构 |
| 架构 | [ARCHITECTURE](ARCHITECTURE.md) | 系统架构总览（组件 / 流程 / 记忆 / HITL / 文件映射） |
| 入门 | [EXPLAIN](EXPLAIN.md) | 10 分钟总览（已瘦身；细节指向 DEEP_DIVE） |
| 深读 | [DEEP_DIVE](DEEP_DIVE.md) | **唯一**函数级详解（配置 → LLM → Agent → RAG → 记忆 → API → MCP → 前端） |
| RAG 设计 | [RAG_DESIGN_ANALYSIS](RAG_DESIGN_ANALYSIS.md) | 设计决策与失败模式（工程版；面试 Q&A 在 interview/） |
| 评估 | [EVALUATION](EVALUATION.md) | 评估指南 + **当前基线** |
| 实验记录 | [EXPERIMENTS](EXPERIMENTS.md) | 历史实验快照（查询改写 / VLM / 图文双通道 / RAG 优化 A/B / 评估体系迭代） |
| Agent 评估 | [AGENT_EVAL](AGENT_EVAL.md) | Agent 路由评估结果（快照，数据见 `data/eval/agent/`） |
| 部署 / 安装 | [DEPLOYMENT](DEPLOYMENT.md) · [SETUP](SETUP.md) | 部署扩展与演进 / 环境搭建 |
| 运维 | [OBSERVABILITY](OBSERVABILITY.md) · [PERFORMANCE](PERFORMANCE.md) | Langfuse 可观测 / 性能压测（快照） |
| 项目 2 | [AGENT_TASK](AGENT_TASK.md) + [task-agent/README](../task-agent/README.md) | 宿主集成 / 独立包 |
| 面试素材 | [docs/interview/](interview/) | RAG Q&A / 评估话术 / 部署决策 |

## 唯一基线（当前，2026-08）

| 指标 | 值 | 口径 |
|------|-----|------|
| 检索 MRR / Hit@1（GT 40 条） | **0.963 / 0.925** | 来源级，最终基线（早期 0.944/0.900 为快照） |
| 生成 Faithfulness / Relevancy | **0.923 / 1.0** | LLM-judge 四指标 |
| 消融 CR | 0.894 → 0.931 → **0.963** | 纯向量 → 混合 → +rerank |
| Agent route@1 / 危险操作拒绝 | **1.0 / 1.0** | 17 条 × 3 采样 |
| 检索 p50 / 吞吐（单 worker） | 82ms / ~16 QPS | 性能快照 |
| Embedding Hit@1（4 模型对比） | **0.975**（bge-small-zh） | 来源级 |
| 测试规模 | **180 单测 / 22 集成 / task-agent 50** | pytest 收集数（2026-08-29） |

> 数字只在本表维护；各篇文档引用本表或标注为历史快照，不再各自维护当前基线。
