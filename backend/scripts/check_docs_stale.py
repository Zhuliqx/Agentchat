"""文档漂移检查：确保 docs/ 与当前代码结构一致。

检测规则为「已废弃 / 已迁移」的符号与路径（见 _RULES），命中即报错退出 1。
每次代码结构重构（拆包 / 迁移函数 / 改接口）后，应同步更新本文件规则，
使文档与代码的漂移在 CI 中被拦截，而不是等读者发现。

用法（backend/ 目录）：
    python scripts/check_docs_stale.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # 项目根

TARGETS = sorted((REPO / "docs").glob("*.md")) + [
    REPO / "README.md",
    REPO / "task-agent" / "README.md",
]

# 重构后必须存在的文档（文档地图 / 实验记录 / 面试素材）
REQUIRED = [
    REPO / "docs" / "README.md",
    REPO / "docs" / "EXPERIMENTS.md",
    REPO / "docs" / "interview" / "README.md",
    REPO / "docs" / "interview" / "rag-qa.md",
    REPO / "docs" / "interview" / "evaluation.md",
    REPO / "docs" / "interview" / "deployment.md",
]

# (说明, 正则)。命中即视为过时表述。
_RULES: list[tuple[str, str]] = [
    ("supervisor 提示词已迁移到 app/agents/prompts.py（build_supervisor_prompt）", r"_build_supervisor_prompt"),
    ("task_agent 已拆为仓库顶层 task-agent/ 独立包", r"backend/app/task_agent/"),
    ("agents/tools.py 已拆为 app/agents/tools/ 包", r"app/agents/tools\.py"),
    ("pymilvus 版本口径为 2.4+（MilvusClient API）", r"pymilvus 3"),
    ("ingestion.py 中的解析/分块函数已迁移到 extractors|chunkers", r"ingestion\.py::(?:split_text|_pdf_extract|load_document|load_text|_html_to_text|_docx_to_text|_read_text_auto|_base_splitter|_build_table_chunks|_build_image_chunks|_build_vlm_chunks|_split_pdf_pages|_chunk_hash)"),
    ("retriever.py 中的后处理纯函数已迁移到 postprocess.py", r"retriever\.py.*(?:_dedupe_and_merge|_dedupe_near_duplicate|_apply_total_budget|_cosine|_merge_multi|_normalize_text)"),
    ("图像多模态编码已迁移到 image_embedding.py", r"from app\.rag\.embedding import get_image_embedder|embedding\.py.*get_image_embedder"),
    ("外部 MCP 配置已改为 JSON（旧 name=url 格式仅兼容）", r"EXTERNAL_MCP_SERVERS=github="),
    ("UserContext 现含 session_id 字段", r"UserContext\(user_id\)"),
    ("_PreludeDedupe 已迁移到 app/agents/streaming.py", r"graph\.py.*_PreludeDedupe|_PreludeDedupe.*graph\.py"),
    ("_RAG_SOURCES 已迁移到 app/agents/tools/sources.py", r"tools\.py.*_RAG_SOURCES|_RAG_SOURCES.*tools\.py"),
    ("同文档限流为 3 条（rag_max_per_doc=3）", r"2 条/文档"),
    ("LLM-judge 四指标自动化闭环已实现（eval_quality + CI rag-quality）", r"尚无.*(?:RAGAS|四指标).*(?:闭环|自动化)"),
    ("RAG 检索工具为 StructuredTool 自建，非 create_retriever_tool", r"create_retriever_tool"),
    ("摄入已改为 PG 先行 + vector_status 状态标记 + 对账任务", r"失败清理已写入块|delete_by_ids\(stale_ids\)"),
    ("PERFORMANCE rerank_score 解释已修正（本次请求写入，非摄入残留）", r"rerank_score 来自摄入"),
    ("Milvus 主键与 Postgres id 的对应关系在 doc_id 字段（非主键一致）", r"向量在 Milvus，id 一致|documents\.id` 一一对应|documents\.id 一一对应"),
    ("文档头部应指向 docs/README.md 文档地图", r"架构文档地图\]\(ARCHITECTURE\.md\) §1"),
    ("EVALUATION_REPORT.md 已并入 EXPERIMENTS.md", r"\]\(EVALUATION_REPORT\.md\)|docs/EVALUATION_REPORT\.md"),
    ("EVALUATION 迭代史已移至 EXPERIMENTS.md", r"### 8\.1 校准一"),
    ("RAG 面试 Q&A 已移至 docs/interview/rag-qa.md", r"### Q1\."),
    ("部署决策记录已移至 docs/interview/deployment.md", r"为什么一开始不直接分布式"),
    ("EXPLAIN 已瘦身为总览（细节在 DEEP_DIVE）", r"## 11\. MCP 深入|## 12\. 记忆机制原理"),
]


def main() -> int:
    missing = [str(p.relative_to(REPO)) for p in REQUIRED if not p.exists()]
    if missing:
        print("文档重构检查失败：缺少必需文档")
        for m in missing:
            print(f"  MISSING: {m}")
        print("请先创建这些文件（见 docs/README.md 文档地图设计）。")
        return 1
    compiled = [(desc, re.compile(pattern)) for desc, pattern in _RULES]
    hits: list[str] = []
    for target in TARGETS:
        if not target.exists():
            continue
        for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
            for desc, rx in compiled:
                if rx.search(line):
                    hits.append(f"{target.relative_to(REPO)}:{lineno}: [{desc}] {line.strip()[:120]}")
    if hits:
        print(f"文档漂移检查失败：{len(hits)} 处过时表述\n")
        for h in hits:
            print(f"  {h}")
        print("\n请同步更新文档（必要时同步更新本脚本的规则）。")
        return 1
    print(f"文档漂移检查通过：{len(TARGETS)} 个文件无过时表述。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
