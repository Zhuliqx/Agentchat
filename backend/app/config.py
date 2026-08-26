"""应用配置中心。

通过 pydantic-settings 从环境变量 / .env 文件加载。
所有连接信息、模型选择、MCP 配置都集中在这里管理。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent                     # 项目根（Agentchat/）


class Settings(BaseSettings):
    """全部可配置项。环境变量大写与字段名一一对应，运行时用 .env 覆盖默认值。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ───────────────────────── 应用 / Web ─────────────────────────
    app_name: str = "Multi-Agent Platform"      # 应用名（影响标题 / 日志）
    log_level: str = "INFO"                     # 日志级别：DEBUG / INFO / WARNING / ERROR
    host: str = "0.0.0.0"                       # 服务监听地址（run.py 读取）
    port: int = 8000                            # 服务监听端口（run.py 读取）
    agent_timeout: float = 120.0                # 单轮对话超时（秒）：LLM / MCP 卡死时避免请求无限挂起
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]  # CORS 显式白名单；避免 "*"+credentials 回显任意 Origin

    # ─────────────────────── Postgres ───────────────────────
    postgres_host: str = "localhost"            # 对话历史 / 会话 / 文档元数据
    postgres_port: int = 5432                   # 数据库端口
    postgres_user: str = "postgres"             # 用户名
    postgres_password: str = "postgres"         # 密码（生产必填）
    postgres_db: str = "agentchat"              # 数据库名

    # ───────────────────────── Milvus ─────────────────────────
    milvus_host: str = "localhost"              # 向量数据库
    milvus_port: int = 19530                    # 端口
    milvus_collection: str = "agent_documents"  # 向量 collection 名
    milvus_uri: str = ""                        # 留空则用 host:port 拼接；也可填 zilliz 云端 uri
    milvus_metric_type: str = "IP"              # 索引度量: IP(内积;向量已归一化时=余弦,最快) / COSINE(自动归一化 query,更防御) / L2

    # ────────────────────── Embedding 模型 ──────────────────────
    embedding_model: str = "BAAI/bge-small-zh-v1.5"  # 本地: BAAI/bge-small-zh-v1.5, sentence-transformers/all-MiniLM-L6-v2；OpenAI 兼容: text-embedding-3-small 等
    embedding_provider: str = "local"           # local | openai
    embedding_dim: int = 512                    # 向量维度（与模型对齐）
    embedding_device: str = "auto"              # auto=有 CUDA 用 cuda 否则 cpu；或显式 cuda / cpu

    # ────────────────────────── LLM ──────────────────────────
    llm_provider: str = "deepseek"              # provider: openai | ollama | deepseek | dashscope
    llm_model: str = ""                         # 仅 ollama 使用；deepseek/dashscope/openai 走各自 *_model 字段
    llm_light_model: str = ""                   # 子 Agent（rag/mcp/search）用本模型；留空则子 Agent 与主模型相同
    llm_timeout: float = 60.0                   # 单次 LLM 请求超时（秒），网络抖动防挂起
    llm_max_retries: int = 2                    # LLM 请求失败重试次数（客户端级）
    agent_cache_enabled: bool = True            # 图执行 / LLM 提示缓存
    subagent_retries: int = 1                   # agent_to_tool 包装的子 Agent 调用失败重试次数

    openai_api_key: str = ""                    # OpenAI（LLM_PROVIDER=openai 时使用）
    openai_base_url: str = "https://api.openai.com/v1"  # OpenAI 兼容端点
    openai_model: str = "gpt-4o-mini"           # 模型名
    temperature: float = 0.3                    # 采样温度

    # ──────────────────────── DeepSeek ────────────────────────
    deepseek_api_key: str = ""                  # DeepSeek API Key（必填）
    deepseek_base_url: str = "https://api.deepseek.com"  # DeepSeek 端点
    deepseek_model: str = "deepseek-chat"       # 模型名

    # ──────────────── DashScope（阿里通义，OpenAI 兼容）────────────────
    dashscope_api_key: str = ""                 # DashScope API Key（必填）
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # DashScope 端点（OpenAI 兼容）
    dashscope_model: str = "qwen-plus"          # 模型名

    # ────────────────────── Tavily 联网搜索 ──────────────────────
    tavily_api_key: str = ""                    # Tavily API Key（必填）
    tavily_max_results: int = 5                 # 最多返回结果数

    # ──────────────────────── MCP 服务器 ────────────────────────
    mcp_db_server_cmd: str = "python"           # 自建 stdio 服务器
    mcp_db_server_args: str = "scripts/db_query_server.py"  # DB 查询脚本
    mcp_time_server_cmd: str = "python"         # 时间服务器命令
    mcp_time_server_args: str = "scripts/time_server.py"  # 时间服务器脚本
    external_mcp_servers: str = ""              # 外部 HTTP/SSE 服务器（可选）：逗号分隔的 "name=url"，；如 "github=http://localhost:8080/mcp"

    # ────────────────────── 原始文件存储 ──────────────────────
    upload_dir: str = "data/uploads"            # 网页上传的原始文档持久保存目录（相对项目根），供下载 / 预览 / 审计
    max_upload_mb: int = 50                     # 上传文件大小上限（MB），超限返回 413

    # ─────────────── 文档解析增强（表格 / 图片 OCR / 图片 VLM）───────────────
    table_extract: bool = False                 # 提取 PDF/DOCX/HTML 表格为结构化块（解决表格乱序 / 截断 / 列语义丢失）
    table_to_text_mode: str = "nl"              # 表格序列化: nl(推荐, embedding 更佳) | markdown
    table_max_rows_per_chunk: int = 10          # 表格分块：每块含表头 + 最多 N 行
    image_ocr_enabled: bool = False             # 对 PDF 内嵌 / 扫描图做 OCR，把图中文字抽出入库
    image_ocr_engine: str = "rapidocr"          # rapidocr(推荐, 免系统依赖) | paddle | tesseract
    image_vlm_enabled: bool = False             # 对图片 / 图表用 VLM 生成语义描述（②）。描述以文本块入现有向量通道，；解决「趋势 / 构图 / 示意图逻辑」等视觉语义不可检索的问题（不改变向量 schema / 模型）。
    image_vlm_provider: str = "deepseek"        # deepseek(官方 v4-flash-vision-exp) | dashscope | openai | ollama
    image_vlm_model: str = "deepseek-v4-flash-vision-exp"  # 视觉模型名
    image_vlm_base_url: str = ""                # 留空则按 provider 推断（deepseek/dashscope/openai 官方端点）
    image_vlm_api_key: str = ""                 # 留空则按 provider 取各自 *_api_key
    image_vlm_max_size: int = 1280              # 送入 VLM 前最长边缩放到此值（控 token / 成本）
    image_vlm_detail: str = "low"               # low(缩放512省token) | high | original | auto

    # ─────────────── 嵌入 / 分块 微调 ───────────────
    embed_batch_size: int = 32                  # 分批嵌入批次大小（大文档防超时 / 显存峰值）
    markdown_strip_headers: bool = True         # Markdown 分块去掉正文标题（标题只存 metadata，减少重复；实测 strip_headers=True 更优）
    doc_level_dedup: bool = False               # 文档级去重：整篇内容（全部块）与已有文档相同 → 跳过

    # ─────────── 图文双通道（③；默认关=行为不变）───────────
    image_dual_channel: bool = False            # 图片用多模态向量（独立 collection）索引，检索时与文本通道融合。
    image_embedding_provider: str = "local"     # local(Chinese-CLIP 等) | dashscope/openai 等兼容端点
    image_embedding_model: str = "OFA-Sys/chinese-clip-vit-base-patch16"  # 多模态模型名
    image_embedding_dim: int = 512              # 图像向量维度（与模型投影维对齐）
    image_channel_top_k: int = 6                # 图像通道候选数（额外召回）
    image_channel_weight: float = 0.4           # 图像通道融合权重（rrf 加权，0~1）

    # ──────────────────────── 检索参数 ────────────────────────
    rag_top_k: int = 4                          # 默认 top_k
    rag_score_threshold: float = 0.35           # 最低相关分阈值
    rag_max_per_doc: int = 3                    # 检索去重：同一文档（source）最多保留的块数（去重 + 相邻块合并后），释放 Top-K 给更多文档
    rag_max_chunk_chars: int = 1500             # 上下文压缩：传给 LLM 的单个块最大字符数
    chunk_size: int = 800                       # 分块长度
    chunk_overlap: int = 100                    # 相邻块重叠

    # ─────────────── 混合检索（向量 + BM25 + RRF）───────────────
    hybrid_search: bool = True                  # 混合检索（向量 + BM25 + RRF）
    bm25_k1: float = 1.5                        # BM25 词频饱和度（越大越不受高频词压制）
    bm25_b: float = 0.75                        # BM25 文档长度归一化（0~1）
    bm25_use_jieba: bool = False                # BM25 中文分词：true 时用 jieba，false 用单字切分
    rrf_k: int = 60                             # RRF 融合常数（越大越平滑）
    hybrid_candidate_k: int = 20                # BM25 关键词通道候选数
    bm25_max_docs: int = 5000                   # 文档块数超过此值时跳过 BM25 通道

    # ──────────────────────── Rerank 精排 ────────────────────────
    rerank_enabled: bool = True                 # 是否启用 rerank 精排
    rerank_model: str = "BAAI/bge-reranker-base"  # rerank 模型
    rerank_candidate_k: int = 6                 # 送入 rerank 的候选数上限
    rerank_max_length: int = 512                # rerank 输入文本截断字符数

    # ──────────────────────── 查询改写 ────────────────────────
    query_rewrite_enabled: bool = False         # mode: none(默认) / rule(规则: 去框架词+泛化并列) / llm(LLM 改写为检索 query, 失败回退原句)
    query_rewrite_mode: str = "rule"            # 改写模式 rule/llm
    query_rewrite_cache_size: int = 512         # 改写结果缓存条目数

    # ───────────── 自适应检索 / 意图路由 / 去重 / 预算（RAG 优化）─────────────
    adaptive_retrieval: bool = False            # 低置信 query 才触发「改写双路 + 放宽候选」，正式 / 高置信 query 走原路。；置信信号 = 初检索最高分（rrf / 向量 score），低于阈值 → 判为「可能召不全」。
    conf_trigger_threshold: float = 0.45        # 低于此分触发自适应
    adaptive_candidate_k: int = 9               # 自适应时放宽 rerank 候选
    intent_routing: bool = False                # 检索级意图路由：按 query 类型调整策略（compare/list/chat/fact）
    dedup_near_duplicate: bool = False          # 语义近似去重（embedding 相似 ≥ 阈值仅留最高分）
    dedup_sim_threshold: float = 0.90           # 语义去重相似阈值
    rag_max_total_chars: int = 0                # 0=不限制；>0 按分数降序累计截断，防超长 context

    # ─────────────────────── Prompt 注入防护 ───────────────────────
    injection_detection_enabled: bool = True    # 检索 / 搜索外部内容按「不可信数据块」隔离（总是生效）；本开关控制注入指令检测：；外部内容命中 → 剔除该块并告警，用户 query 命中 → 拒绝请求
    injection_llm_review: bool = False          # 规则命中后用 LLM 复核再剔除
    injection_output_filter: bool = True        # 输出侧泄露检测

    # ────────────────────── 自主任务 Agent ──────────────────────
    task_agent_mode: str = "replan"             # 规划模式: fixed=一次性计划 / replan=每步动态重规划
    task_agent_hitl: bool = True                # 节点级人工确认
    task_agent_max_retries: int = 2             # verify 容错: 单个子任务失败后，自检（LLM 判是否重试）的最大重试次数

    # ────────────────────── 模型离线加载（HuggingFace）──────────────────────
    hf_offline: bool = True                     # embedding / rerank 已本地缓存时置 True，需下载新模型时临时设 False

    # ──────────────────────── 长期记忆（Store）────────────────────────
    memory_semantic_search: bool = True         # 语义检索需要 Postgres 启用 pgvector 扩展
    memory_dedup_threshold: float = 0.86        # remember_memory 语义去重阈值（余弦相似度高于此值视为重复，更新而非新增）

    # ──────────────── Human-in-the-Loop（人工确认）────────────────
    hitl_enabled: bool = True                   # 总开关
    hitl_actions: list[str] = []                # HITL 模式：；默认 [](空) = LLM 自主判定：注册 request_confirmation 工具，由模型根据操作；影响自主决定是否请求用户确认；非空（如 ["mcp"]）= 强制确认：对应动作调用前无条件 interrupt（confirm_before）；；有前端开关的动作（search/rag/remember）开关打开时自动豁免。；注：search=联网搜索 | rag=知识库检索 | mcp=MCP工具 | remember=保存长期记忆

    # ───────────── 代码 Agent（受限沙箱执行 Python）─────────────
    code_agent_enabled: bool = True  # 是否启用代码执行 Agent
    code_exec_timeout: float = 15.0             # 单段代码执行超时（秒），settrace 计时中断
    code_exec_max_output: int = 8000            # stdout/stderr 截断上限（字符）

    # ───────────── 可观测性（Langfuse，可选；三个变量都配置才启用）─────────────
    langfuse_host: str = ""                     # Langfuse 地址（须配 3 个才启用）
    langfuse_public_key: str = ""               # Langfuse public key
    langfuse_secret_key: str = ""               # Langfuse secret key

    # ─────────────── 用户 / 认证（JWT）───────────────
    auth_secret: str = "dev-secret-change-me-in-env-0123456789abcdef"  # 生产环境务必在 .env 中设置强随机 auth_secret（≥32 字节）；默认值仅用于本地开发，；使 token 在后端重启后仍有效（临时密钥会导致每次重启全部登出）。
    guest_user_id: str = "default"              # 未登录访客使用的默认用户 id（保持现有单用户体验不破坏）
    admin_usernames: str = ""                   # 管理员用户名（逗号分隔，如 "admin,zhangsan"）；命中者可在管理后台查看 / 删除用户

    # ─────────────────── 派生属性 / 工具方法（读取上方字段）───────────────────
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
