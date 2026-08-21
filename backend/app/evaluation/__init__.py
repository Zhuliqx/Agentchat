"""RAG 质量评估包(自研 LLM-judge,对齐 RAGAS 口径)。

模块:
- judge_llm.py : 评审模型(DeepSeek,温度 0, JSON mode)与各指标 prompt
- metrics.py   : 四指标(context_precision/context_recall/faithfulness/answer_relevancy)聚合
- dataset.py   : ground truth 加载与校验

供 scripts/eval_quality.py(端到端跑分)与 scripts/eval_rag.py(检索级回归)使用。
"""
from __future__ import annotations

import sys


def setup_utf8_stdio() -> None:
    """统一 stdout/stderr 为 UTF-8，避免 Windows 控制台 GBK 乱码/编码崩溃。

    根因：Windows 默认代码页是 GBK，若 Python 以 UTF-8 输出、控制台按 GBK 解释，
    中文/符号会乱码；反之若打印 GBK 不支持的字符（✓❌⚠ 等）会直接 UnicodeEncodeError。
    这里强制 Python 侧输出 UTF-8；控制台侧请执行 `chcp 65001` 或
    `[Console]::OutputEncoding = [Text.Encoding]::UTF8` 与之对齐。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - 环境不支持时忽略
                pass
