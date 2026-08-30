# 文档地图与基线

> 本仓库两个项目（**项目 1 · Agentchat** 主应用、**项目 2 · task-agent** 独立包）的全部文档索引与**唯一基线数字**。
> 最后校验：2026-08-29（防漂移检查见 `backend/scripts/check_docs_stale.py`）

## 文档类型速查

| 类型 | 文档 | 说明 |
|------|------|------|
| 入口 | [README](../README.md)（仓库根） | 项目概览 + 评估与质量 + 快速开始 |
| 架构 | [ARCHITECTURE](ARCHITECTURE.md) | 系统架构总览（组件 / 流程 / 记忆 / HITL / 文件映射） |
| 入门 | [EXPLAIN](EXPLAIN.md) | 10 分钟总览（实现细节以代码注释与 [ARCHITECTURE](ARCHITECTURE.md) 为准） |
| 可复现评估 | [REPRODUCIBLE_EVAL](REPRODUCIBLE_EVAL.md) | 公开示例语料 + 14 问评估集，检索级 MRR/Hit@1 可复现（含步骤） |
| 部署 / 安装 | [DEPLOYMENT](DEPLOYMENT.md) · [SETUP](SETUP.md) | 部署扩展与演进 / 环境搭建 |
| 运维 | [OBSERVABILITY](OBSERVABILITY.md) | Langfuse 可观测性接入 |
| 项目 2 | [AGENT_TASK](AGENT_TASK.md) + [task-agent/README](../task-agent/README.md) | 宿主集成 / 独立包 |

## 唯一基线（当前，2026-08）

| 指标 | 值 | 口径 |
|------|-----|------|
| 检索 MRR / Hit@1（GT 40 条） | **0.963 / 0.925** | 来源级，最终基线（早期 0.944/0.900 为快照） |
| 生成 Faithfulness / Relevancy | **0.923 / 1.0** | LLM-judge 四指标 |
| 消融 CR | 0.894 → 0.931 → **0.963** | 纯向量 → 混合 → +rerank |
| Agent route@1 / 危险操作拒绝 | **1.0 / 1.0** | 17 条 × 3 采样 |
| 检索 p50 / 吞吐（单 worker） | 82ms / ~16 QPS | 性能快照 |
| Embedding Hit@1（4 模型对比） | **0.975**（bge-small-zh） | 来源级 |
| 测试规模 | **183 单测 / 22 集成 / task-agent 78** | pytest 收集数（2026-08-30）；单测覆盖率 app 41% / task-agent 87% |

> 数字只在本表维护；各篇文档引用本表或标注为历史快照，不再各自维护当前基线。
