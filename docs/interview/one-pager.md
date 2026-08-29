# 简历项目 One-pager（面试速览）

> 用途：投简历 / 面试开场。两个项目各一页，先讲"是什么 + 数字"，再讲"我做了什么"。
> 数字均来自真实评估（唯一基线见 [docs/README.md](../README.md)）。

## 电梯演讲（30 秒）

> "我做了两个可运行的项目：一个企业级 RAG 多 Agent 平台（FastAPI + LangGraph + Milvus），
> 用自建的 LLM-judge 评估体系驱动每一个技术决策——检索 Hit@1 100%、四指标 0.9+、
> 230+ 测试全绿；另一个是把它的核心 Agent 能力抽出来的独立包（零业务依赖、可离线运行），
> 用依赖注入把 LLM / Checkpointer / 执行器全部做成接口缝。两个项目都跑在真实数据上，
> 有完整评估闭环、可观测性和多租户安全隔离。"

---

## 项目 1 · Agentchat —— 企业级 RAG 多 Agent 知识问答平台

### 定位
FastAPI + LangGraph + LangChain 的多 Agent 平台：**RAG（混合检索 + rerank）+ MCP 工具 + 三层记忆 + HITL 人工确认 + Time Travel**，前端 Vue3 深色主题。

### 架构一图
```
前端 (Vue3) ──> FastAPI ──> Supervisor (LangGraph)
                              ├── rag_agent   → Milvus 向量 + PG BM25 → RRF → rerank
                              ├── mcp_agent   → Postgres / 时间 / 外部 MCP
                              ├── web_search  → Tavily（直接工具）
                              ├── code_agent  → 受限沙箱执行 Python
                              └── 记忆        → Checkpointer（短期）+ Store（长期语义记忆）
可观测：Langfuse trace 全链路 ｜ 安全：Prompt 注入防护 + 多租户数据隔离 + 只读 SQL 校验
```

### 量化成果（唯一基线）
| 维度 | 数字 |
|---|---|
| RAG 检索质量 | MRR **0.963** / 来源 Hit@1 **100%**（40 条真实难例） |
| 生成质量 | Faithfulness **0.923** / Relevancy **1.0** |
| Agent 路由 | Route@1 **1.0**（真实 LLM 评估） |
| 性能 | 检索 p50 **82ms**、~**16 QPS**（单 worker）；SSE 首 token ~19ms |
| 工程质量 | 单元 **180** + 集成 **22** + task-agent 包 **50**；CI：Ruff + Pyright + 检索回归 + 文档漂移检查 |

### 我的核心贡献
- **评估体系工程化**：自建 LLM-as-judge 四指标（对齐 RAGAS 口径），并校准了三轮（截断 400→800 修复召回低估 8~11pp；relevancy 指引修复对比/筛选误判，Rel 0.885→1.0）；NDCG 评估改走生产同路径后发现排序层真实瓶颈。
- **数据驱动决策**：查询改写、自适应检索等 8+ 实验开关全部 A/B 实证，用评估数据决定默认值（如改写默认关）。
- **检索管线设计**：BM25 + 向量 + RRF 混合检索、rerank 候选限流、上下文压缩管线（去重/合并/预算）、图文双通道。
- **一致性设计**：Postgres 事实源 + Milvus 派生索引 + 对账任务自愈（本次实际清理 192 条幽灵 doc_id）。
- **Agent 工程**：HITL（interrupt/Command resume）、Time Travel 分叉、流式去重（开场白缓冲 + _PreludeDedupe）、可观测（Langfuse）。

---

## 项目 2 · task-agent —— 可独立运行的自主任务 Agent 引擎

### 定位
把项目 1 的长任务 Agent 能力抽成**仓库顶层独立 Python 包**（src 布局、零 `app.*` 依赖），宿主通过适配器注入 LLM / Checkpointer / 执行器；包内自带纯 LLM 默认执行器与离线 demo（脚本化 FakeLLM，无需 API key）。

### 架构一图（接口缝）
```
宿主应用（Agentchat）──适配器注入──> task-agent 包（LangGraph 图）
  get_llm("light")          │           ├── plan/replan：LLM 拆解目标
  get_checkpointer()         │           ├── execute：注入的 Executor 执行一步
  run_agent 执行器           │           ├── check：完成度判断
                             │           ├── verify：失败自检重试（max_retries）
                             │           └── final：结构化交付
                             └── 每步支持 HITL 确认（proceed/edit/skip）+ Time Travel
```

### 量化成果
- **50 个包内单测**（解析/路由/HITL/verify/error_handler/Time Travel）全绿，CI 独立跑
- 依赖仅 `langgraph>=1.0`；离线 demo `python -m task_agent.demo` 可复现全流程
- 可切换 OpenAI 兼容端点（`OPENAI_BASE_URL` / `API_KEY`）直接跑真实 LLM

### 我的核心贡献
- **接口缝设计**：`TaskAgentConfig`（frozen dataclass）+ `LLMFactory` + `CheckpointerProvider` + `Executor(ExecuteRequest → StepResult)` 四个注入点，引擎与业务完全解耦。
- **节点级容错**：retry_policy + timeout + error_handler（返回 Command 续跑）、verify 语义重试、findings reducer。
- **可演示交付**：脚本化 FakeLLM 离线全流程 + 结构化输出，简历可讲、面试可当场跑。

---

## 一句话区分
项目 1 证明**工程深度**（评估闭环、可观测、安全、数据驱动）；项目 2 证明**抽象与复用能力**（独立包、依赖注入、可迁移）。面试叙事主线：**"先做深，再抽出来做成能独立运行的东西。"**
