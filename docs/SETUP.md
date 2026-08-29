# 环境搭建（Setup）

> 相关文档：见 [文档地图](README.md)；项目 2 见 [AGENT_TASK](AGENT_TASK.md)。
> 最后校验：2026-08-29（文档与当前代码同步；防漂移检查见 `backend/scripts/check_docs_stale.py`）

本文档说明如何在 Windows（Docker Desktop 运行数据库）上把项目跑起来。

## 1. 启动数据库（Docker Desktop）

本项目的 Postgres 与 Milvus 运行在 **Docker Desktop** 中。两种方式：

### 方式 A：使用项目自带的 docker-compose（推荐，一键拉起全部）

在项目根目录打开终端：

```powershell
docker compose up -d
docker compose ps
```

会启动：

| 服务 | 端口 | 说明 |
|------|------|------|
| postgres | 5432 | 关系数据库（pgvector 镜像，内置向量扩展） |
| milvus-standalone | 19530 | 向量数据库 |
| milvus-etcd / milvus-minio | - | Milvus 依赖 |

> 首次启动 Milvus 需要下载镜像，等 `docker compose ps` 显示 healthy 后再继续。

> **关于 pgvector**：Postgres 使用 `pgvector/pgvector:pg16` 镜像，用于 LangGraph Store 的
> **长期记忆语义检索**。如果此前用的是 `postgres:16` 容器，想启用语义检索：
> ```powershell
> docker compose down
> # 编辑 docker-compose.yml 确认 image 为 pgvector/pgvector:pg16
> docker compose up -d
> ```
> 数据卷（pg_data）保留，无需重灌数据。不重建也能正常运行——Store 会自动降级为关键词检索。

### 方式 B：已有运行中的实例

如果你已经有一套 Postgres / Milvus，直接在 `backend/.env` 里改连接信息即可，
**不要**重复启动 compose 中的服务。

```powershell
# 检查端口是否被监听
Test-NetConnection -ComputerName localhost -Port 19530
Test-NetConnection -ComputerName localhost -Port 5432
```

## 2. 安装 Python 依赖

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` 含本地路径依赖 `-e ../task-agent`（项目 2 独立包，随主项目一起安装）。
首次安装 `sentence-transformers` 会自动下载 torch，体积较大，请耐心等待。
（如网络受限，可考虑只用 OpenAI 的 embedding：见第 3 步。）

## 3. 配置环境变量

```powershell
Copy-Item .env.example .env
# 然后用编辑器打开 .env 修改
```

关键项：

- **LLM**：默认 `LLM_PROVIDER=deepseek`，已配好 `DEEPSEEK_API_KEY`。想切换：
  - **DashScope（通义千问）**：`LLM_PROVIDER=dashscope`，`DASHSCOPE_MODEL=qwen-plus`
  - **OpenAI**：`LLM_PROVIDER=openai`，填 `OPENAI_API_KEY`
  - **Ollama 本地**：`LLM_PROVIDER=ollama`，需先 `ollama pull qwen2.5:7b`
- **联网搜索**：`TAVILY_API_KEY` 已配置，前端勾选"联网搜索"即可使用实时搜索。
- **Embedding**：默认本地 `BAAI/bge-small-zh-v1.5`（首次运行自动下载）。也可改为 `EMBEDDING_PROVIDER=openai`。
- **rerank**：默认开启 `RERANK_ENABLED=true`，模型 `BAAI/bge-reranker-base`（首次 RAG 检索自动下载约 1.1GB，应用启动后在后台线程预热；可设 `RERANK_ENABLED=false` 关闭）。
- **混合检索**：默认开启 `HYBRID_SEARCH=true`（向量 + BM25 + RRF），检索质量更高。
- **图片语义描述 / 图文双通道（可选）**：
  -  图片语义描述：`IMAGE_VLM_ENABLED=true`，默认 `IMAGE_VLM_PROVIDER=deepseek`（`deepseek-v4-flash-vision-exp`）复用 `DEEPSEEK_API_KEY`，无需额外下载；`IMAGE_VLM_DETAIL`（默认 `low`）可选 `high/original/auto` 控制图片处理与成本；
  -  图文双通道：需先下载多模态模型再开启 `IMAGE_DUAL_CHANNEL=true`：
    ```powershell
    huggingface-cli download OFA-Sys/chinese-clip-vit-base-patch16
    ```
  -  **推荐**：图片能力建议 **图片语义描述+图文双通道 同时开启**（`IMAGE_VLM_ENABLED=true` + `IMAGE_DUAL_CHANNEL=true`）——实测在含图 GT 达 MRR 1.000，优于仅图文双通道。
- **人工确认（HITL）**：默认 **LLM 自主判定**（`HITL_ACTIONS=[]`，类似 Claude Code/Codex）——注册 `request_confirmation` 工具，由模型根据操作影响自主决定是否请求用户授权；低风险/只读操作直接执行。需要**强制确认**时设 `HITL_ACTIONS=["mcp"]`（调用前无条件中断；有开关的动作如 search/rag 开关打开时自动豁免）。设 `HITL_ENABLED=false` 完全关闭。
- **容错**：`AGENT_CACHE_ENABLED=true`（图执行/LLM 提示缓存）、`SUBAGENT_RETRIES=1`（子 Agent 重试）。模型调用超时/重试由 LLM 客户端（`LLM_TIMEOUT`/`LLM_MAX_RETRIES`）与统一 middleware（超时 + 日志）负责。
- **模型离线加载**：`HF_OFFLINE=true`（默认）——embedding/rerank 已本地缓存时直接离线加载，避免 HF 网络不可达时启动/首次请求联网 HEAD 卡住重试；需下载新模型时临时设 `false`。
- **日志与上传**：`LOG_LEVEL=INFO`（DEBUG/INFO/WARNING/ERROR）、`MAX_UPLOAD_MB=50`（上传文档大小上限）。
- **外部 MCP**（可选）：`EXTERNAL_MCP_SERVERS={"github": "http://localhost:8080/mcp"}`（JSON；兼容旧 `name=url` 逗号格式）

## 4. 初始化数据库

```powershell
python scripts/init_db.py
```

- 创建 Postgres 表（sessions / messages / documents）
- 创建 Milvus collection（`agent_documents`）
- Checkpointer / Store 所需的表由应用启动时（lifespan）自动创建，无需手动操作

## 5. 摄入知识库文档

把文档放到一个目录（支持 txt / md / pdf / docx / html），然后：

```powershell
# 摄入单个文件
python scripts/ingest_docs.py .\docs\sample.md

# 摄入整个目录
python scripts/ingest_docs.py D:\knowledge_base
```

也可以在网页端左侧"知识库文档"处直接拖拽上传。

> 网页上传的**原始文件会持久保存**到项目根 `data/uploads/<uuid>/`，点击文档名可在
> 弹窗中**预览/下载**原始文件；删除文档时原始文件一并清理（`data/uploads/` 已加入 gitignore）。
> 用 `ingest_docs.py` 摄入的本地文件则保留在原位置，不复制。

> 提供一份示例文档：
> ```powershell
> New-Item -ItemType Directory -Force D:\桌面\Agentchat\data\kb
> Set-Content -Encoding UTF8 D:\桌面\Agentchat\data\kb\company.md -Value @"
> # 公司介绍
> 示例科技有限公司成立于 2020 年，主营 AI 平台研发...
> 我们的旗舰产品是多 Agent 协作平台，支持 RAG 与 MCP 扩展...
> 公司目前有 120 名员工，总部位于北京。
> "@
> python scripts/ingest_docs.py data\kb
> ```

## 6. 启动后端

```powershell
cd backend
python run.py        # Windows 下自动使用 SelectorEventLoop（Checkpointer 必需）
```

> 注意：Windows 上不要直接 `uvicorn app.main:app`，否则 Checkpointer（psycopg 异步）会因
> ProactorEventLoop 失败。`run.py` 已通过自定义 loop factory 解决。

启动后：
- 前端页面：<http://localhost:8000>（frontend-v2 构建产物；若尚未构建请先看 6.1 节）
- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

### 6.1 前端（frontend-v2，Vue 3 + Vite）

前端位于 `frontend-v2/`（**Vue 3 + Vite + TypeScript + Tailwind CSS 4 + Pinia**）。
两种运行方式：

**方式 A：生产模式（推荐，构建产物由 FastAPI 托管）**

```powershell
cd frontend-v2
npm install          # 首次
npm run build        # 类型检查 + 构建，产物输出到 dist/
```

后端 `app/main.py` 会自动挂载 `frontend-v2/dist`，刷新 <http://localhost:8000> 即可使用。

**方式 B：开发模式（Vite dev server，热更新）**

```powershell
cd frontend-v2
npm install
npm run dev          # http://localhost:5173，/api 自动代理到 :8000
```

浏览器打开 <http://localhost:5173>，代码改动即时热更新。

**常用脚本（`frontend-v2/package.json`）**

| 命令 | 说明 |
|------|------|
| `npm run dev` | 开发服务器（:5173，`/api` 代理 → :8000） |
| `npm run build` | 生产构建（`vue-tsc -b` 类型检查 + `vite build` → `dist/`） |
| `npm run typecheck` | 仅类型检查 |
| `npm run test` | Vitest 单元测试（SSE 解析 / markdown / chat store） |

> 侧边栏底部 ☀/🌙 图标可切换暗/亮主题（localStorage 持久化）；侧边栏可拖拽调宽、收窄自动折叠。
> 开发模式下请先启动后端（第 6 节），否则聊天/会话等接口会失败。

## 7. 验证

1. 打开 <http://localhost:8000>，左下角状态点应为绿色，显示已连接的 MCP 服务器数。
2. 发一条消息，例如"知识库中有什么内容？"，应看到 `rag_agent` 被调度，事件实时滚动、工具调用前先出现完整开场白、工具完成后答案**逐 token 实时流出**（SSE token 级流式）。
3. 再试"帮我统计一下数据库里有多少个会话"，应看到 `mcp_agent` 调用 `db_query_postgres` 工具。
4. （可选）上传一个文档后点击文档名，弹窗中可**预览/下载原始文件**（存于 `data/uploads/`）。

## 8. 测试

**单元测试**（纯逻辑，不依赖外部服务）：

```powershell
cd backend
pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest tests/unit -q
```

覆盖：BM25、SQL 只读校验、分块/解析、RRF、混合检索后处理、查询改写、Prompt 注入、Agent 流式/路由、
图文双通道、配置、向量库一致性等；另有顶层 `task-agent/` 独立包测试（`pytest task-agent/tests -q`）。

**API 集成测试**（需运行中的 Postgres/Milvus/MCP 依赖，即 Docker 服务已启动）：

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration -v
```

覆盖：健康检查、会话 CRUD + 批量删除（含 checkpoint 清理）、记忆 CRUD、RAG 上传/检索/删除、chat 与 HITL（中断 → 409 → 清理）。DB 不可达时自动跳过；对话/HITL 用例在未配置 LLM key 时单独跳过。

## 常见问题

| 问题 | 排查 |
|------|------|
| `向量库初始化失败` | `docker compose ps` 确认 milvus healthy；检查 19530 端口 |
| 连接 Postgres 失败 | 确认 compose 中 postgres healthy；检查 `.env` 账号密码 |
| `ollama connection refused` | 启动 Ollama 应用，`ollama pull qwen2.5:7b` |
| 首次 embedding / rerank 慢 | 本地模型首次运行需下载（rerank 约 1.1GB），之后有缓存 |
| MCP 服务器启动失败 | 用 `python run.py` 从 backend 目录启动（脚本路径相对 backend） |
| 请求长时间无响应 | 检查 `.env` 的 `AGENT_TIMEOUT`（默认 120s）；LLM key 是否有效 |
| 长期记忆只能关键词检索 | Postgres 非 pgvector 镜像；重建容器即可启用语义检索（见第 1 节） |
