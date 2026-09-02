"""配置字段按域分组（纯字段声明，供 ``app.config.Settings`` 聚合继承）。

- 保持字段名不变：env 变量（大写字段名）与 ``settings.xxx`` 访问方式
  均与拆分前一致，``.env`` 无需迁移；
- 每个分组一个 ``BaseModel`` 子类，只声明字段与注释，不包含逻辑；
- 派生属性（DSN / 设备解析 / 外部 MCP 解析等）仍在 ``app.config.Settings`` 上。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, field_validator


# 默认关的实验性增强开关（A/B 验证后启用）。键名与字段名一致，供
# 清单/报告/未来运行时开关界面使用；实验开关字段集中在
# ``RetrievalEnhancementsSection`` 与文档解析/图文双通道分组中。
EXPERIMENTAL_SWITCHES: tuple[str, ...] = (
    "query_rewrite_enabled",
    "adaptive_retrieval",
    "intent_routing",
    "dedup_near_duplicate",
    "rag_max_total_chars",
    "rag_multi_turn_context",
    "embed_with_context",
    "rerank_section_context",
    "rag_front_load_best",
    "pdf_page_meta",
    "table_extract",
    "image_ocr_enabled",
    "image_vlm_enabled",
    "image_dual_channel",
)


class AppSection(BaseModel):
    """应用 / Web。"""

    app_name: str = "Multi-Agent Platform"      # 应用名（影响标题 / 日志）
    log_level: str = "INFO"                     # 日志级别：DEBUG / INFO / WARNING / ERROR
    host: str = "127.0.0.1"                     # 服务监听地址（run.py 读取）
    port: int = 8000                            # 服务监听端口（run.py 读取）
    agent_timeout: float = 120.0                # 单轮对话超时（秒）：LLM / MCP 卡死时避免请求无限挂起
    agent_max_tool_calls: int = 20              # 单轮对话工具调用上限（0=不限制；防失控循环烧 token）
    agent_max_model_calls: int = 25             # 单轮对话模型调用上限（0=不限制；超限直接结束本轮）
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]  # CORS 显式白名单；避免 "*"+credentials 回显任意 Origin


class PostgresSection(BaseModel):
    """Postgres（对话历史 / 会话 / 文档元数据）。"""

    postgres_host: str = "localhost"            # 数据库地址
    postgres_port: int = 5432                   # 端口
    postgres_user: str = "postgres"             # 用户名
    postgres_password: str = "postgres"         # 密码（生产必填）
    postgres_db: str = "agentchat"              # 数据库名


class MilvusSection(BaseModel):
    """Milvus（向量库）。"""

    milvus_host: str = "localhost"              # 地址
    milvus_port: int = 19530                    # 端口
    milvus_collection: str = "agent_documents"  # collection 名
    milvus_uri: str = ""                        # 留空则用 host:port 拼接；也可填 zilliz 云端 uri
    milvus_metric_type: str = "IP"              # 索引度量: IP / COSINE / L2


class EmbeddingSection(BaseModel):
    """Embedding 模型。"""

    embedding_model: str = "BAAI/bge-small-zh-v1.5"  # 本地: bge-small-zh；OpenAI 兼容: text-embedding-3-small 等
    embedding_provider: str = "local"           # local | openai
    embedding_dim: int = 512                    # 向量维度（与模型对齐）
    embedding_device: str = "auto"              # auto=有 CUDA 用 cuda 否则 cpu；或显式 cuda / cpu


class LLMSection(BaseModel):
    """LLM（provider / 模型 / 超时重试 / API key）。"""

    llm_provider: str = "deepseek"              # provider: openai | ollama | deepseek | dashscope
    llm_model: str = ""                         # 仅 ollama 使用；deepseek/dashscope/openai 走各自 *_model 字段
    llm_light_model: str = ""                   # 子 Agent（rag/mcp/search）用本模型；留空则与主模型相同
    llm_timeout: float = 60.0                   # 单次 LLM 请求超时（秒），网络抖动防挂起
    llm_max_retries: int = 2                    # LLM 请求失败重试次数（客户端级）
    agent_cache_enabled: bool = True            # 图执行 / LLM 提示缓存
    subagent_retries: int = 1                   # agent_to_tool 包装的子 Agent 调用失败重试次数

    openai_api_key: str = ""                    # OpenAI
    openai_base_url: str = "https://api.openai.com/v1"  # OpenAI 兼容端点
    openai_model: str = "gpt-4o-mini"           # 模型名
    temperature: float = 0.3                    # 采样温度

    deepseek_api_key: str = ""                  # DeepSeek API Key（必填）
    deepseek_base_url: str = "https://api.deepseek.com"  # DeepSeek 端点
    deepseek_model: str = "deepseek-chat"       # 模型名

    dashscope_api_key: str = ""                 # DashScope API Key（必填）
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # DashScope 端点（OpenAI 兼容）
    dashscope_model: str = "qwen-plus"          # 模型名


class HistorySection(BaseModel):
    """对话历史自动压缩（SummarizationMiddleware）"""

    history_summary_enabled: bool = True        # 总开关（默认开；关掉则图不带摘要中间件）
    history_summary_trigger_tokens: int = 8000  # 触发阈值：历史 token ≥ 此值 且 消息数 ≥ min_messages
    history_summary_min_messages: int = 30      # 触发阈值：消息数下限（避免短对话被压缩）
    history_summary_keep_messages: int = 20     # 压缩后保留的最近消息条数
    history_summary_max_input_tokens: int = 3000  # 喂给摘要模型的输入截断上限（控成本）


class TavilySection(BaseModel):
    """Tavily 联网搜索。"""

    tavily_api_key: str = ""                    # API Key（必填）
    tavily_max_results: int = 5                 # 最多返回结果数


class MCPSection(BaseModel):
    """MCP 服务器（自建 stdio + 外部 HTTP）。"""

    mcp_db_server_cmd: str = "python"           # 自建 stdio 服务器
    mcp_db_server_args: str = "scripts/db_query_server.py"  # DB 查询脚本
    mcp_time_server_cmd: str = "python"         # 时间服务器命令
    mcp_time_server_args: str = "scripts/time_server.py"  # 时间服务器脚本
    external_mcp_servers: dict[str, str] = {}   # 外部 HTTP/SSE 服务器 {name: url}（env 传 JSON）

    @field_validator("external_mcp_servers", mode="before")
    @classmethod
    def _parse_external_mcp(cls, v: object) -> dict[str, str]:
        """接受结构化 JSON（新格式）或旧版逗号分隔 "name=url,..."（兼容迁移）。"""
        if isinstance(v, dict):
            return {str(k).strip(): str(val).strip() for k, val in v.items() if k and val}
        if v is None:
            return {}
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return {}
            if s.startswith("{"):
                try:
                    data = json.loads(s)
                    if isinstance(data, dict):
                        return {
                            str(k).strip(): str(val).strip()
                            for k, val in data.items()
                            if k and val
                        }
                except (ValueError, TypeError):
                    pass  # 非 JSON → 按旧格式解析
            result: dict[str, str] = {}
            for item in s.split(","):
                item = item.strip()
                if not item:
                    continue
                name, _, url = item.partition("=")
                if name and url:
                    result[name.strip()] = url.strip()
            return result
        return {}


class UploadSection(BaseModel):
    """原始文件存储。"""

    upload_dir: str = "data/uploads"            # 上传目录（相对项目根），供下载 / 预览 / 审计
    max_upload_mb: int = 50                     # 上传文件大小上限（MB），超限返回 413


class DocParsingSection(BaseModel):
    """文档解析增强（表格 / 图片 OCR / 图片 VLM）。"""

    table_extract: bool = False                 # 提取 PDF/DOCX/HTML 表格为结构化块
    table_to_text_mode: str = "nl"              # 表格序列化: nl | markdown
    table_max_rows_per_chunk: int = 10          # 表格分块：每块含表头 + 最多 N 行
    image_ocr_enabled: bool = False             # 对 PDF 内嵌 / 扫描图做 OCR，把图中文字抽出入库
    image_ocr_engine: str = "rapidocr"          # rapidocr | paddle | tesseract
    image_vlm_enabled: bool = False             # 对图片 / 图表用 VLM 生成语义描述
    image_vlm_provider: str = "deepseek"        # deepseek | dashscope | openai | ollama
    image_vlm_model: str = "deepseek-v4-flash-vision-exp"  # 视觉模型名
    image_vlm_base_url: str = ""                # 留空则按 provider 推断
    image_vlm_api_key: str = ""                 # 留空则按 provider 取各自 *_api_key
    image_vlm_max_size: int = 1280              # 送入 VLM 前最长边缩放到此值（控 token / 成本）
    image_vlm_detail: str = "low"               # detail：low(省token) | high | original | auto


class ChunkingSection(BaseModel):
    """嵌入 / 分块 微调。"""

    embed_batch_size: int = 32                  # 分批嵌入批次大小（大文档防超时 / 显存峰值）
    markdown_strip_headers: bool = True         # Markdown 分块去掉正文标题（标题只存 metadata）
    doc_level_dedup: bool = False               # 文档级去重：整篇内容与已有文档相同 → 跳过
    chunk_size: int = 800                       # 分块长度
    chunk_overlap: int = 100                    # 相邻块重叠


class ImageDualSection(BaseModel):
    """图文双通道（默认关）。"""

    image_dual_channel: bool = False            # 图片用多模态向量索引，检索时与文本通道融合
    image_embedding_provider: str = "local"     # local(Chinese-CLIP 等) | dashscope/openai 等兼容端点
    image_embedding_model: str = "OFA-Sys/chinese-clip-vit-base-patch16"  # 多模态模型名
    image_embedding_dim: int = 512              # 图像向量维度（与模型投影维对齐）
    image_channel_top_k: int = 6                # 图像通道候选数（额外召回）
    image_channel_weight: float = 0.4           # 图像通道融合权重（rrf 加权，0~1）


class RetrievalSection(BaseModel):
    """检索参数（混合检索 / rerank / 上下文压缩）。"""

    rag_top_k: int = 4                          # 默认 top_k
    rag_score_threshold: float = 0.35           # 最低相关分阈值
    rag_max_per_doc: int = 3                    # 同文档（source）最多保留的块数（去重 + 相邻块合并后）
    rag_max_chunk_chars: int = 1500             # 传给 LLM 的单个块最大字符数
    hybrid_search: bool = True                  # 混合检索（向量 + BM25 + RRF）
    bm25_k1: float = 1.5                        # BM25 词频饱和度
    bm25_b: float = 0.75                        # BM25 文档长度归一化（0~1）
    bm25_use_jieba: bool = False                # BM25 中文分词：true 用 jieba，false 单字切分
    rrf_k: int = 60                             # RRF 融合常数
    hybrid_candidate_k: int = 20                # BM25 关键词通道候选数
    bm25_max_docs: int = 5000                   # 文档块数超过此值时跳过 BM25 通道
    rerank_enabled: bool = True                 # 是否启用 rerank 精排
    rerank_model: str = "BAAI/bge-reranker-base"  # rerank 模型
    rerank_candidate_k: int = 6                 # 送入 rerank 的候选数上限
    rerank_max_length: int = 512                # rerank 输入文本截断字符数
    rerank_batch_size: int = 4                  # rerank 批推理 batch_size（CPU 友好）


class RetrievalEnhancementsSection(BaseModel):
    """检索增强（默认关，A/B 验证后启用；见 EXPERIMENTAL_SWITCHES）。"""

    query_rewrite_enabled: bool = False         # 查询改写开关（rule/llm；默认关）
    query_rewrite_mode: str = "rule"            # 改写模式 rule/llm
    query_rewrite_cache_size: int = 512         # 改写结果缓存条目数
    adaptive_retrieval: bool = False            # 低置信 query 才触发「改写双路 + 放宽候选」
    conf_trigger_threshold: float = 0.45        # 低于此分触发自适应
    adaptive_candidate_k: int = 9               # 自适应时放宽 rerank 候选
    intent_routing: bool = False                # 检索级意图路由（compare/list/chat/fact）
    dedup_near_duplicate: bool = False          # 语义近似去重（embedding 相似 ≥ 阈值仅留最高分）
    dedup_sim_threshold: float = 0.90           # 语义去重相似阈值
    rag_max_total_chars: int = 0                # 0=不限制；>0 按分数降序累计截断
    rag_multi_turn_context: bool = False        # 检索时把最近几轮会话历史拼进 query
    embed_with_context: bool = False            # 嵌入侧给块文本加章节/文件名前缀（需 force_reingest）
    rerank_section_context: bool = False        # rerank pair 文本带上章节/文件名前缀
    rag_front_load_best: bool = False           # 相关块前置（lost-in-the-middle 缓解）
    pdf_page_meta: bool = False                 # PDF 按页分块并记录 metadata["page"]


class InjectionSection(BaseModel):
    """Prompt 注入防护。"""

    injection_detection_enabled: bool = True    # 注入指令检测（外部内容命中 → 剔除；用户 query 命中 → 拒绝）
    injection_llm_review: bool = False          # 规则命中后用 LLM 复核再剔除
    injection_output_filter: bool = True        # 输出侧泄露检测


class TaskAgentSection(BaseModel):
    """自主任务 Agent。"""

    task_agent_mode: str = "replan"             # 规划模式: fixed=一次性计划 / replan=每步动态重规划
    task_agent_hitl: bool = True                # 节点级人工确认
    task_agent_max_retries: int = 2             # verify 容错: 单个子任务失败后自检的最大重试次数
    task_agent_max_steps: int = 8               # replan 模式步数上限（防循环）


class HFSection(BaseModel):
    """模型离线加载（HuggingFace）。"""

    hf_offline: bool = True                     # embedding / rerank 已本地缓存时置 True


class MemorySection(BaseModel):
    """长期记忆（Store）。"""

    memory_semantic_search: bool = True         # 语义检索需要 Postgres 启用 pgvector 扩展
    memory_dedup_threshold: float = 0.86        # remember 语义去重阈值（余弦相似度）


class HITLSection(BaseModel):
    """Human-in-the-Loop（人工确认）。"""

    hitl_enabled: bool = True                   # 总开关
    hitl_actions: list[str] = []                # 空=LLM 自主判定；非空（如 ["mcp"]）= 强制确认


class CodeAgentSection(BaseModel):
    """代码 Agent（受限沙箱执行 Python）。"""

    code_agent_enabled: bool = True             # 是否启用代码执行 Agent
    code_exec_timeout: float = 15.0             # 单段代码执行超时（秒）
    code_exec_max_output: int = 8000            # stdout/stderr 截断上限（字符）


class ObservabilitySection(BaseModel):
    """可观测性（Langfuse，可选；三个变量都配置才启用）。"""

    langfuse_host: str = ""                     # Langfuse 地址
    langfuse_public_key: str = ""               # public key
    langfuse_secret_key: str = ""               # secret key


class AuthSection(BaseModel):
    """用户 / 认证（JWT）。"""

    auth_secret: str = "dev-secret-change-me-in-env-0123456789abcdef"  # 生产务必在 .env 设置强随机值
    guest_user_id: str = "default"              # 未登录访客使用的默认用户 id
    admin_usernames: str = ""                   # 管理员用户名（逗号分隔）
