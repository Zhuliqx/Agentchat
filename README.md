# Multi-Agent Platform

[![CI](https://img.shields.io/github/actions/workflow/status/Zhuliqx/Agentchat/ci.yml?branch=main&label=CI)](https://github.com/Zhuliqx/Agentchat/actions)
[![License](https://img.shields.io/github/license/Zhuliqx/Agentchat)](https://github.com/Zhuliqx/Agentchat/blob/main/LICENSE)
[![PyPI - task-agent](https://img.shields.io/pypi/v/agentchat-task-agent?label=PyPI%20-%20task-agent)](https://pypi.org/project/agentchat-task-agent/)

一个基于 **FastAPI + LangGraph + LangChain** 的多 Agent 平台，集成 **RAG**（向量检索问答）与 **MCP**（模型上下文协议工具），使用 **Milvus**（向量库）+ **PostgreSQL**（关系库），前端为 **Vue 3 + Vite + TypeScript + Tailwind CSS 4** 打造的现代深色主题界面。

> 最后校验：2026-08-29（文档与当前代码同步；防漂移检查见 `backend/scripts/check_docs_stale.py`）

## 评估与质量

> 全部数字来自真实评估（GT 40 条 / LLM-judge / 压测 / 多采样）；完整语料为私有，
> 公开示例语料的可复现基线见 [docs/REPRODUCIBLE_EVAL.md](docs/REPRODUCIBLE_EVAL.md)。

| 领域 | 关键指标 | 结果 | 一句话结论 |
|------|---------|------|-----------|
| 检索质量（GT 40 条） | MRR / Hit@1 | **0.963 / 0.925** | 混合检索 + rerank，来源级命中（唯一基线见 [docs/README](docs/README.md)） |
| 生成质量 | Faithfulness / Relevancy | **0.923 / 1.0** | LLM-judge 四指标，低幻觉（[唯一基线](docs/README.md)） |
| 消融（每层价值） | CR：纯向量→混合→+rerank | 0.894 → 0.931 → **0.963** | 每加一层都有量化收益（[唯一基线](docs/README.md)） |
| Agent 编排 | route@1 / 危险操作拒绝 | **1.0 / 1.0** | 首次路由全对、危险操作全 HITL/拒绝（[唯一基线](docs/README.md)） |
| 性能（单 worker） | 检索 p50 / 吞吐 | 82ms / ~16 QPS | 单机满足小团队，扩展触发信号明确 |
| 流式对话 | SSE TTFB / 总耗时 | ~19ms / ~5s | 首 token 即时，瓶颈在 LLM 生成 |
| Embedding 选型 | Hit@1（4 模型） | **0.975**（bge-small） | “更大不更好”实证，现用模型最优（[唯一基线](docs/README.md)） |
| 数据驱动决策 | 查询改写 | **默认关** | 检索侧无增益 + 端到端微降，触发式启用 |
| 工程质量 | 单测 / 集成 | **183 / 22**（另有 task-agent 独立包 **78**；单测覆盖率 app 41% / task-agent 87%） | CI 挂检索回归 + LLM-judge 质量评估 + 文档漂移检查（Ruff + pytest） |
| 可复现示例 | 示例语料检索基线 | **MRR 1.000 / Hit@1 1.000** | 仓库自带 5 文件语料 + 14 问评估集，clone 后可复现（[步骤](docs/REPRODUCIBLE_EVAL.md)） |

## 项目构成

本仓库包含两个可独立使用的部分：

### 1. Agentchat —— 多 Agent 知识问答平台

FastAPI + LangGraph + LangChain 构建的知识问答平台：**RAG（混合检索 + rerank）+ MCP 工具 + 三层记忆 + HITL 人工确认 + Time Travel**，前端 Vue3 深色主题。架构与设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

### 2. task-agent —— 自主任务 Agent 独立包

把长任务 Agent 能力抽成的**独立 Python 包/独立仓库**（src 布局、零业务依赖，发行名 `agentchat-task-agent`，`pip install` 即可用）：接收模糊目标 → LLM 分解/每步重规划 → 循环执行 → 结构化交付。提供 CLI（`task-agent run`）、事件流（on_event → 宿主 SSE）、内置工具执行器、跨任务记忆与 LLM-judge 任务质量评估。设计见 [docs/AGENT_TASK.md](docs/AGENT_TASK.md)，独立仓库见 [github.com/Zhuliqx/task-agent](https://github.com/Zhuliqx/task-agent)。

## 演示

后端运行中执行 `python backend/scripts/demo_showcase.py`，依次展示：RAG 多轮问答 + 引用溯源 / HITL 人工确认恢复 / task-agent 长任务执行 / Time Travel checkpoint 历史。

## 特性

- **多 Agent 编排**：Supervisor 层级模式，自动路由到 RAG Agent / web_search 搜索工具 / MCP Agent，支持任意组合的多步工具调用
- **RAG**：文档上传（txt/md/pdf/docx/html）→ 分块（Markdown 按标题切分）→ 向量化 → **混合检索**（向量 + BM25 + RRF）→ **rerank 精排**（可选 **查询改写** `rule`/`llm`，默认关）→ LLM 生成，中文友好（默认 `bge-small-zh-v1.5`）；**原始文件持久保存**（`data/uploads/`，可在线预览/下载）。**解析增强**：PDF 用 `pdfplumber→pymupdf→pypdf` 回退、Markdown 去标题（默认开）；**图片能力**：可选 **图片语义描述**（VLM 转图内容为文本）与 **图文双通道**（多模态向量 + 文本融合，见 [ARCHITECTURE](docs/ARCHITECTURE.md)）
- **联网搜索**：Tavily 直接搜索工具（`web_search`），实时获取最新网络资讯（LangChain 官方推荐工具）
- **代码 Agent**：受限沙箱执行 Python（子进程隔离 + 超时 kill + 危险能力禁用 + 模块白名单 + 输出截断），需要实际计算/验证算法/数据处理时由 Supervisor 自动调度；`CODE_AGENT_ENABLED` / `CODE_EXEC_TIMEOUT` 可配置
- **MCP**：
  - 自建 MCP 服务器（数据库查询、时间/计算），stdio 方式
  - 支持接入任意外部 MCP 服务器（streamable http），一行配置
- **多 LLM 支持**：DeepSeek / 阿里通义(DashScope) / OpenAI / Ollama，切换只需改一个配置项
- **运行时模型切换**：前端顶栏下拉实时切换模型（DeepSeek Chat / Reasoner、通义 qwen-plus/max/turbo 等），按 `.env` 已配置的 API key 自动列出可用模型，选择持久化、立即生效（切换时自动清空 LLM/图缓存）
- **持久化**：会话、消息历史、文档元数据存 Postgres；向量存 Milvus
- **三层记忆**（LangGraph 官方机制）：
  - 短期记忆（会话内）：**Checkpointer**（`AsyncPostgresSaver`，`thread_id=session_id`）持久化图状态，多轮对话自动连续
  - 运行时上下文（仅当次调用）：**`context_schema=UserContext`** + `context=` 传入，不持久化，工具经 `Runtime` 访问
  - 长期记忆（跨会话）：**Store**（`AsyncPostgresStore`）按 namespace 持久化，Agent 经 `remember_memory` / `recall_memory` 读写，前端记忆面板管理
  - 支持**语义检索**（pgvector 索引）与**写入去重**（相似记忆合并覆盖）
- **流式输出（SSE）**：`POST /api/chat/stream` **token 级流式**——Agent 调度事件实时推送，工具调用前先一次性推送完整开场白、工具完成后的答案逐 token 推送（自动去重重复前缀），前端实时渲染；同步 DB 调用放线程池，不阻塞事件循环
- **人工确认（HITL）**：基于 LangGraph `interrupt`/`Command(resume)` 机制，前端弹出确认卡片，用户确认/取消后从断点继续（同一 `thread_id`）。**默认 LLM 自主判定**（类似 Claude Code/Codex）：由模型根据操作影响自主决定是否请求用户授权（`request_confirmation` 工具）；也可配置 `HITL_ACTIONS` 切换为**强制确认**（调用前无条件中断；有开关的动作在开关打开时自动豁免）
- **版本历史（Time Travel）**：基于 Checkpointer 的 checkpoint 版本链，前端可查看会话**每一步的历史状态**（时间线 + 摘要），并**从任意历史步骤分叉重新生成**（产生新分支，不影响原历史）；`GET /api/sessions/{id}/checkpoints` 拉取历史，`/api/chat(/stream)` 传 `checkpoint_id` 触发分叉
- **用户系统（JWT）**：注册 / 登录 / 会话与长期记忆**按用户隔离**；未登录访客自动归入 `default` 用户（不破坏单用户体验）；密码使用 PBKDF2-HMAC-SHA256 哈希，JWT HS256 签名
- **知识库按用户隔离**：文档（Postgres + Milvus 向量）按 `user_id` 隔离，不同用户的知识库互不可见（上传/检索/删除/预览均校验归属）；`ingest_docs.py` 可用 `--user` 指定归属用户
- **Prompt 注入防护**：检索/搜索外部内容按「不可信数据块」隔离；中英规则库检测命中即剔除+告警，用户 query 含注入指令直接拒绝（`INJECTION_DETECTION_ENABLED`）；可选 LLM 复核降误报（`INJECTION_LLM_REVIEW`）；输出侧泄露检测（系统提示词片段/密钥模式，`INJECTION_OUTPUT_FILTER`）
- **会话数据分析**：`GET /api/sessions/{id}/stats` 返回消息数/回合数/Token 估算/平均回复长度/对话时长等，前端「📊 分析」面板可视化
- **定时 / 批处理任务**：后台 asyncio 调度器（无第三方依赖）按 `interval:<秒>` 或 `cron:<分钟>` 执行任务；内置重建知识库索引、清理孤儿 Checkpoint、清理失效文档三类任务，可手动触发、启停、删除（前端「⏱ 任务」面板）
- **增量摄入 / 去重**：文档分块按内容指纹（sha256）增量摄入，未变化的块不重复嵌入/写入，整篇无变化则零写入
- **多模型路由**：`LLM_LIGHT_MODEL` 配置轻量模型后，Supervisor 用主模型、子 Agent（RAG/搜索/MCP）用轻量模型，成本更低
- **统一错误响应**：所有异常统一返回 JSON `{"detail", "code"}`，前端可读、不出现 HTML 500
- **健壮性**：单轮请求超时（默认 120s）、LLM 请求超时/重试、rerank 模型后台预热
- **容错**：模型调用统一 `middleware`（超时 + 耗时日志）、LLM 客户端网络重试（`LLM_MAX_RETRIES`）、子 Agent 调用自动重试（`SUBAGENT_RETRIES`）、图执行/LLM 提示缓存（`AGENT_CACHE_ENABLED`）、模型**离线加载**（`HF_OFFLINE=true`，HF 网络不可达时直接走本地缓存不联网检查）
- **现代前端（frontend-v2）**：Vue 3 + Vite + TypeScript + Tailwind CSS 4 + Pinia；marked 官方 highlight 集成（marked-highlight + highlight.js 按需注册）+ DOMPurify 消毒；SSE 流式渲染、Agent 编排轨道（Orbit）、HITL 确认卡片、Time Travel 分叉；Vitest 单元测试。构建产物由 FastAPI 托管

## 技术栈

| 领域 | 技术 |
|------|------|
| 后端 | FastAPI, Uvicorn, Pydantic v2 |
| Agent | LangGraph, LangChain, DeepSeek/DashScope/OpenAI/Ollama |
| 记忆 | LangGraph Checkpointer + Store (Postgres, 支持语义检索) |
| 向量库 | Milvus (+ pymilvus) |
| 检索 | 向量 + BM25 + RRF 混合检索；CrossEncoder rerank（候选数受限）；查询改写（rule/llm，可开关） |
| 关系库 | PostgreSQL, SQLAlchemy |
| 联网搜索 | Tavily (`langchain-tavily`) |
| MCP | mcp SDK (FastMCP + client) |
| 嵌入 | sentence-transformers / OpenAI Embeddings |
| 前端 | Vue 3 + Vite + TypeScript + Tailwind CSS 4 + Pinia + marked/highlight.js（frontend-v2） |
| 认证 | 自实现 JWT（HS256）+ PBKDF2 密码哈希（stdlib，零依赖） |
| 可观测 | Langfuse 自托管（trace `supervisor→子Agent→工具→LLM`，三配置齐全即启用，fail-open） |

## 目录结构

```
Agentchat/
├── docker-compose.yml        # Docker Desktop 一键启动 Postgres + Milvus
├── backend/
│   ├── run.py               # 启动入口（Windows 用，保证 Checkpointer 正常）
│   ├── requirements.txt / requirements-dev.txt / Dockerfile
│   ├── app/
│   │   ├── main.py           # FastAPI 入口（托管前端 + API + 模型预热）
│   │   ├── config.py         # 配置中心（字段分组在 config_sections.py）
│   │   ├── api/routes/       # chat / sessions / rag / memory / health / auth / tasks / admin / search / agent-tasks
│   │   ├── agents/           # LangGraph 多 Agent（graph / llm / prompts / streaming / tools 包）
│   │   ├── agents/task_agent_adapter.py # 项目2宿主适配器（引擎为独立包 agentchat-task-agent）
│   │   ├── rag/              # 嵌入(image_embedding) / 向量库 / BM25 / 混合检索 / rerank / 摄入(extractors+chunkers) / postprocess
│   │   ├── db/               # Postgres 模型、会话管理、Checkpointer/Store
│   │   ├── mcp_integration/  # MCP 服务器 + 客户端管理器
│   │   ├── evaluation/       # 评估（LLM-judge / 四指标聚合 / 指纹去重）
│   │   ├── security.py       # 密码哈希 + JWT（用户认证）
│   │   ├── observability.py  # Langfuse 可观测（handler 工厂 + fail-open）
│   │   ├── scheduler.py      # 定时/批处理任务调度器（asyncio，零依赖）
│   │   ├── event_loop.py     # Windows ProactorEventLoop 兼容修复
│   │   └── schemas/          # Pydantic 请求/响应模型
│   ├── data/                 # 应用运行时数据（model_choice.json + eval/ 评估产物）
│   ├── tests/                # pytest 测试（unit/ 单元 + integration/ 集成）
│   │   ├── unit/             # 纯逻辑单元测试（BM25 / 分块 / 评估 / RRF / 流式去重…）
│   │   └── integration/      # 需 Postgres+Milvus 的集成测试（test_api / 检索回归）
│   ├── scripts/              # init_db / ingest_docs / smoke_test / eval_rag / check_docs_stale / MCP 服务器入口
│   └── .env.example
├── frontend-v2/              # 前端（Vue 3 + Vite + TS + Tailwind 4）
├── data/                     # 用户内容（知识库文档 / 上传文件）
│   ├── kb/                   # 示例知识库文档
│   └── uploads/              # 网页上传的原始文件（自动生成，可下载/预览）
└── docs/
    ├── README.md             # 文档地图与唯一基线
    ├── ARCHITECTURE.md       # 系统架构总览
    ├── SETUP.md              # 环境搭建指南
    ├── DEPLOYMENT.md         # 部署与扩展（单机取舍 / 演进）
    ├── OBSERVABILITY.md      # Langfuse 可观测性
    ├── REPRODUCIBLE_EVAL.md  # 可复现评估（示例语料 + 步骤）
    ├── EXPLAIN.md            # 项目详解（10 分钟总览）
    └── AGENT_TASK.md         # 项目2·自主任务 Agent 设计文档
```

## 快速开始

完整步骤见 [docs/SETUP.md](docs/SETUP.md)，核心五步：

```powershell
# 1. 启动数据库（Docker Desktop）
docker compose up -d

# 2. 安装依赖 + 配置
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # 编辑 .env（默认 DeepSeek；也可切 DashScope/OpenAI/Ollama）

# 3. 初始化数据库 + 摄入文档
python scripts/init_db.py
python scripts/ingest_docs.py D:\your_docs_folder

# 4. 构建前端（frontend-v2，首次）
cd ..\frontend-v2
npm install
npm run build

# 5. 启动（Windows 用 run.py，保证 Checkpointer 正常工作）
cd ..\backend
python run.py
```

> 项目 2 · 自主任务 Agent 已拆为**独立仓库**（发行名 `agentchat-task-agent`），
> 地址：[github.com/Zhuliqx/task-agent](https://github.com/Zhuliqx/task-agent)。

打开 <http://localhost:8000> 即可开始对话。API 文档在 <http://localhost:8000/docs>。

## API 端点速查

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 非流式对话（兼容旧客户端） |
| POST | `/api/chat/stream` | **SSE token 级流式**对话（前端默认使用） |
| POST | `/api/auth/register` | 注册用户（用户名 2-32 位，密码 ≥6 位） |
| POST | `/api/auth/login` | 登录，返回 JWT token |
| GET | `/api/auth/me` | 当前登录用户（需 Bearer token） |
| GET/POST | `/api/sessions` | 会话列表 / 新建会话（按登录用户隔离） |
| GET/PATCH/DELETE | `/api/sessions/{id}` | 会话历史 / 重命名 / 删除 |
| GET | `/api/sessions/{id}/stats` | **会话数据分析**（消息数/回合/token/时长等） |
| GET | `/api/sessions/{id}/checkpoints` | **版本历史（Time Travel）**：会话 checkpoint 时间线 |
| GET | `/api/sessions/{id}/export` | 导出会话为 Markdown |
| POST | `/api/sessions/batch-delete` | 批量删除会话（含消息与 checkpoint） |
| GET/POST | `/api/tasks` | 定时任务列表 / 新建 |
| GET | `/api/models` | 可用模型列表 + 当前选择（运行时模型切换） |
| PUT | `/api/models/current` | 切换当前模型（持久化 + 清缓存，立即生效） |
| PATCH/DELETE | `/api/tasks/{id}` | 修改（启停/调度）/ 删除任务 |
| POST | `/api/tasks/{id}/run` | 立即执行一次任务 |
| GET | `/api/tasks/registry` | 可用任务类型 |
| POST | `/api/rag/upload` | 上传文档（原始文件持久保存到 `data/uploads/`） |
| POST | `/api/rag/search?query=` | 检索测试接口（混合检索 + 可选 rerank，与 RAG Agent 同路径） |
| GET | `/api/rag/documents` | 文档列表（含 `has_file` 标识） |
| GET | `/api/rag/documents/file?source=` | 预览/下载原始文件 |
| DELETE | `/api/rag/documents?source=` | 删除文档（含原始文件） |
| GET/POST | `/api/memory` | 长期记忆列表 / 添加（支持 `?query=` 语义检索） |
| DELETE | `/api/memory/{id}` | 删除单条记忆 |
| GET | `/api/health` | 健康检查（Postgres / Milvus / MCP） |

## 关键配置项（`backend/.env`）

| 配置 | 默认 | 说明 |
|------|------|------|
| `LLM_PROVIDER` | `deepseek` | deepseek / dashscope / openai / ollama |
| `TAVILY_API_KEY` | - | 联网搜索（为空则 `web_search` 工具不可用） |
| `RERANK_ENABLED` | `true` | 检索后 rerank 精排（首次下载约 1.1GB 模型） |
| `QUERY_REWRITE_ENABLED` | `false` | 查询改写总开关（rule 零依赖 / llm 需 LLM；A/B 实证默认关） |
| `QUERY_REWRITE_MODE` | `rule` | 改写档位：`none` / `rule` / `llm` |
| `HYBRID_SEARCH` | `true` | 混合检索（向量 + BM25 + RRF） |
| `AGENT_TIMEOUT` | `120` | 单轮对话超时（秒） |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 本地向量模型 |
| `EMBEDDING_DEVICE` | `auto` | 推理设备：auto=有 CUDA 用 cuda 否则 cpu（embedding 与 rerank 共用），或显式 cuda/cpu |
| `INJECTION_DETECTION_ENABLED` | `true` | Prompt 注入检测（外部内容命中→剔除，用户 query 命中→400） |
| `INJECTION_LLM_REVIEW` | `false` | 规则命中后用 LLM 复核再剔除（降误报，有成本） |
| `INJECTION_OUTPUT_FILTER` | `true` | 输出泄露检测（系统提示词片段/密钥模式，仅告警） |
| `AGENT_CACHE_ENABLED` | `true` | 图执行/LLM 提示缓存（相同输入命中跳过重复 LLM 调用） |
| `SUBAGENT_RETRIES` | `1` | 子 Agent 调用失败重试次数 |
| `HF_OFFLINE` | `true` | 模型离线加载（HF 网络不可达时避免联网 HEAD 卡住） |
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `MAX_UPLOAD_MB` | `50` | 上传文档大小上限（MB），超限返回 413 |
| `AUTH_SECRET` | 开发默认值 | JWT 签名密钥（**生产务必改为强随机值**） |
| `LLM_LIGHT_MODEL` | 空 | 子 Agent 轻量模型（配置后 Supervisor 用主模型、子 Agent 用轻量模型） |

## 冒烟测试与单元测试

服务启动后，可一键验证三类 Agent 是否正常：

```powershell
cd backend
.\venv\Scripts\python.exe scripts/smoke_test.py
```

依次测试：健康检查 → RAG 知识库问答 → MCP 数据库查询 → 联网搜索。

单元测试（纯逻辑，不依赖外部服务）：

```powershell
pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest tests/unit -q
```

覆盖：BM25 索引、SQL 只读校验、文档分块、RRF 融合、LLM 路由（`LLM_LIGHT_MODEL`）、内容指纹去重、JWT/密码哈希、调度表达式。

API 集成测试（需运行中的 Postgres/Milvus/MCP 依赖；覆盖会话 CRUD + 批量删除、记忆 CRUD、RAG 上传/检索/删除、chat 与 HITL 中断/409；DB 不可达时自动跳过）：

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration -v
```

新功能端到端验证（认证 → 会话隔离 → 统计 → 任务系统，需后端运行中）：

```powershell
.\venv\Scripts\python.exe scripts/verify_auth_tasks.py
```

RAG 检索评估（固定问题集 top-k 命中率，需 Postgres + Milvus 运行中）：

```powershell
.\venv\Scripts\python.exe scripts/eval_rag.py
```

性能压测（检索链路/完整对话）：

```powershell
.\venv\Scripts\python.exe scripts/benchmark.py --endpoint search --concurrency 4 --total 200
.\venv\Scripts\python.exe scripts/benchmark.py --endpoint chat --concurrency 2 --total 6
```

查询改写 A/B：`--rewrite rule|llm` 跑实验档，`--compare A.json B.json` 输出逐条对比（胜/负/平 + Hit@K + 改写对照）。

CI（`.github/workflows/ci.yml`）：Ruff 检查（F 级错误）→ Pyright 类型检查（非阻塞）→ 单元测试。

## License

MIT
