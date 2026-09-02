"""应用配置中心。

通过 pydantic-settings 从环境变量 / .env 文件加载。
所有连接信息、模型选择、MCP 配置都集中在这里管理。

结构（v2）：字段按域分组声明在 ``app.config_sections``（纯 BaseModel 分组），
``Settings`` 聚合继承——字段名不变，因此 env 变量名（大写字段名）与
``settings.xxx`` 访问方式均与拆分前一致，``.env`` 无需迁移。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config_sections import (
    AppSection,
    AuthSection,
    ChunkingSection,
    CodeAgentSection,
    DocParsingSection,
    EmbeddingSection,
    HFSection,
    HistorySection,
    HITLSection,
    ImageDualSection,
    InjectionSection,
    LLMSection,
    MCPSection,
    MemorySection,
    MilvusSection,
    ObservabilitySection,
    PostgresSection,
    RetrievalEnhancementsSection,
    RetrievalSection,
    TaskAgentSection,
    TavilySection,
    UploadSection,
)

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent                     # 项目根（Agentchat/）


class Settings(
    BaseSettings,
    AppSection,
    PostgresSection,
    MilvusSection,
    EmbeddingSection,
    LLMSection,
    HistorySection,
    TavilySection,
    MCPSection,
    UploadSection,
    DocParsingSection,
    ChunkingSection,
    ImageDualSection,
    RetrievalSection,
    RetrievalEnhancementsSection,
    InjectionSection,
    TaskAgentSection,
    HFSection,
    MemorySection,
    HITLSection,
    CodeAgentSection,
    ObservabilitySection,
    AuthSection,
):
    """全部可配置项。环境变量大写与字段名一一对应，运行时用 .env 覆盖默认值。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
