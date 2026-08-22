"""应用配置中心。

通过 pydantic-settings 从环境变量 / .env 文件加载。
所有连接信息、模型选择、MCP 配置都集中在这里管理。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent  # 项目根（Agentchat/）


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "Multi-Agent Platform"
    log_level: str = "INFO"  # 日志级别：DEBUG / INFO / WARNING / ERROR
    host: str = "0.0.0.0"
    port: int = 8000
    # 单轮对话超时（秒）：LLM/MCP 卡死时避免请求无限挂起
    agent_timeout: float = 120.0

    # ---- Postgres（对话历史 / 会话 / 文档元数据）----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "agentchat"

    # ---- Milvus（向量数据库）----
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "agent_documents"
    milvus_uri: str = ""  # 留空则用 host:port 拼接；也可填 zilliz 云端 uri

    # ---- Embedding 模型 ----
    # 本地模型: BAAI/bge-small-zh-v1.5, sentence-transformers/all-MiniLM-L6-v2
    # OpenAI 兼容: text-embedding-3-small 等
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_provider: str = "local"  # local | openai
    embedding_dim: int = 512
    # 推理设备: auto=有 CUDA 用 cuda 否则 cpu（推荐，部署无 GPU 机器自动回退）; 或显式 cuda/cpu
    embedding_device: str = "auto"

    def resolved_embedding_device(self) -> str:
        """解析实际推理设备：auto=有 CUDA 用 cuda，否则 cpu；显式指定则原样返回。"""
        device = self.embedding_device
        if device != "auto":
            return device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    # ---- LLM ----
    # provider: openai | ollama | azure_openai | deepseek | dashscope
    # 默认 deepseek 与 .env.example 一致；漏配时不会静默连到 ollama
    llm_provider: str = "deepseek"
    llm_model: str = ""  # 仅 ollama 使用；deepseek/dashscope/openai 走各自 *_model 字段
    # 可选轻量模型（如 deepseek-chat 的轻量档）：配置后 Supervisor 用主模型、
    # 子 Agent（rag/mcp/search）用本模型；留空则子 Agent 与主模型相同
    llm_light_model: str = ""
    llm_timeout: float = 60.0  # 单次 LLM 请求超时（秒），网络抖动防挂起
    llm_max_retries: int = 2  # LLM 请求失败重试次数（客户端级）
    # 图执行/LLM 提示缓存（create_agent cache）：相同输入命中，跳过重复 LLM 调用。
    # 注意：工具类问题（搜索/RAG）在数据变化后可能返回缓存旧答案；命中率低，风险可控。
    agent_cache_enabled: bool = True
    # agent_to_tool 包装的子 Agent 调用失败重试次数
    subagent_retries: int = 1
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    temperature: float = 0.3

    # ---- Azure OpenAI ----
    azure_endpoint: str = ""
    azure_api_version: str = "2024-02-15-preview"
    azure_deployment: str = ""  # 模型部署名

    # ---- DeepSeek ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"  # 或 deepseek-reasoner

    # ---- DashScope（阿里通义，OpenAI 兼容）----
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-plus"  # 或 qwen-max / qwen-turbo

    # ---- Tavily 联网搜索 ----
    tavily_api_key: str = ""
    tavily_max_results: int = 5

    # ---- 自建 MCP 服务器 ----
    # stdio 方式运行，脚本在 backend/scripts/ 下
    mcp_db_server_cmd: str = "python"
    mcp_db_server_args: str = "scripts/db_query_server.py"
    mcp_time_server_cmd: str = "python"
    mcp_time_server_args: str = "scripts/time_server.py"

    # ---- 外部 MCP 服务器（HTTP/SSE，可选）----
    # 逗号分隔的 "name=url" 列表，如 "github=http://localhost:8080/mcp"
    external_mcp_servers: str = ""

    # ---- 原始文件存储 ----
    # 网页上传的原始文档持久保存目录（相对项目根），供下载/预览/审计
    upload_dir: str = "data/uploads"
    # 上传文件大小上限（MB），超限返回 413
    max_upload_mb: int = 50

    # ---- 检索参数 ----
    rag_top_k: int = 4
    rag_score_threshold: float = 0.35
    # 检索去重：同一文档(source)最多保留的块数（去重 + 相邻块合并后），释放 Top-K 给更多文档
    rag_max_per_doc: int = 3
    # 上下文压缩：传给 LLM 的单个块最大字符数（超长截断，减少噪音 token）
    rag_max_chunk_chars: int = 1500
    chunk_size: int = 800
    chunk_overlap: int = 100
    # 混合检索（向量 + BM25 + RRF）
    hybrid_search: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    # BM25 中文分词：true 时用 jieba（需安装），false 用单字切分
    bm25_use_jieba: bool = False
    rrf_k: int = 60
    hybrid_candidate_k: int = 20  # BM25 关键词通道候选数
    bm25_max_docs: int = 5000  # 文档块数超过此值时跳过 BM25 通道（防内存/CPU 爆炸）
    # rerank（检索后精排）
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_top_k: int = 4
    rerank_candidate_k: int = 6  # 送入 rerank 的候选数上限（控制 CPU 推理量；越小越快）
    rerank_max_length: int = 512  # rerank 输入文本截断字符数（减少 token）
    # 检索查询改写（Query Rewriting）：改善「口语查询 × 书面文档」的语义鸿沟。
    # mode: none(默认,原样) / rule(规则:去框架词+泛化并列,零依赖,CI 可挂) /
    #       llm(LLM 改写为检索 query,失败回退原句)
    # 改写后与「原 query」双路检索兜底，防改写丢失信息（见 retriever._expand_queries）
    query_rewrite_enabled: bool = False
    query_rewrite_mode: str = "rule"
    query_rewrite_cache_size: int = 512

    # ---- Prompt 注入防护 ----
    # 检索/搜索外部内容按「不可信数据块」隔离（总是生效）；本开关控制注入指令检测：
    # 外部内容命中→剔除该块并告警，用户 query 命中→拒绝请求（见 app/rag/prompt_injection.py）
    injection_detection_enabled: bool = True
    # 规则命中后用 LLM 复核再剔除（进一步降误报；有 LLM 调用成本，默认关）
    injection_llm_review: bool = False
    # 输出侧泄露检测（系统提示词片段/密钥模式；零成本正则，常开，仅告警不改回答）
    injection_output_filter: bool = True

    # 模型离线加载：embedding/rerank 已本地缓存时置 True，避免启动时联网 HEAD 检查
    # 卡住（HF 网络不可达场景）。需下载新模型时临时设 False。
    hf_offline: bool = True

    # ---- 长期记忆（Store）----
    # 语义检索需要 Postgres 启用 pgvector 扩展（docker-compose 已用 pgvector 镜像）
    memory_semantic_search: bool = True
    # remember_memory 语义去重阈值（余弦相似度高于此值视为重复，更新而非新增）
    memory_dedup_threshold: float = 0.86

    # ---- Human-in-the-Loop（人工确认）----
    # 基于 LangGraph interrupt/Command(resume) 实现；需 Checkpointer 支持。
    # 机制保留（默认启用）；但有前端开关控制的动作（联网/知识库/记忆）在对应
    # 开关打开时自动豁免——开关即用户授权，不再逐次确认。HITL 实际只作用于
    # 无开关的外部操作（如 mcp），由 HITL_ACTIONS 指定。
    hitl_enabled: bool = True  # 总开关
    # HITL 模式：
    #   默认 []（空）= LLM 自主判定：注册 request_confirmation 工具，由模型根据操作
    #                   影响自主决定是否请求用户确认（类似 Claude Code / Codex 的授权设计）。
    #   非空（如 ["mcp"]）= 强制确认：对应动作调用前无条件 interrupt（confirm_before）；
    #                   有前端开关的动作（search/rag/remember）开关打开时自动豁免。
    #   注：search=联网搜索 | rag=知识库检索 | mcp=MCP工具 | remember=保存长期记忆
    hitl_actions: list[str] = []

    # ---- 代码 Agent（受限沙箱执行 Python）----
    code_agent_enabled: bool = True  # 是否启用代码执行 Agent
    code_exec_timeout: float = 15.0  # 单段代码执行超时（秒），settrace 计时中断
    code_exec_max_output: int = 8000  # stdout/stderr 截断上限（字符）

    # ---- 可观测性（Langfuse，可选；三个变量都配置才启用）----
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ---- CORS（显式白名单；避免 "*"+credentials 回显任意 Origin）----
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    # ---- 用户 / 认证（JWT）----
    # 生产环境务必在 .env 中设置强随机 auth_secret（≥32 字节）；默认值仅用于本地开发，
    # 使 token 在后端重启后仍有效（临时密钥会导致每次重启全部登出）。
    auth_secret: str = "dev-secret-change-me-in-env-0123456789abcdef"
    # 预留：token 有效期（分钟）。注意 create_token 当前未签发 exp，此配置暂未生效
    token_expire_minutes: int = 60 * 24 * 7
    # 未登录访客使用的默认用户 id（保持现有单用户体验不破坏）
    guest_user_id: str = "default"
    # 管理员用户名（逗号分隔，如 "admin,zhangsan"）；命中者可在管理后台查看/删除用户
    admin_usernames: str = ""

    @property
    def milvus_connection_uri(self) -> str:
        if self.milvus_uri:
            return self.milvus_uri
        return f"http://{self.milvus_host}:{self.milvus_port}"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_conninfo(self) -> str:
        """psycopg 原生连接串（用于 langgraph-checkpoint-postgres）。"""
        return self.postgres_dsn.replace("+psycopg", "")

    @property
    def external_mcp_dict(self) -> dict[str, str]:
        """解析外部 MCP 配置为 {name: url}。"""
        result: dict[str, str] = {}
        for item in self.external_mcp_servers.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, url = item.partition("=")
            if name and url:
                result[name.strip()] = url.strip()
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
