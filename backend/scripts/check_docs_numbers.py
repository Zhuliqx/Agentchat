"""文档数字一致性检查：README 的「评估与质量」表与 docs/README 的唯一基线必须一致。

README 是门面（保留头条数字），docs/README 是唯一基线；两处手工维护会漂移，
所以在 CI 里比对——不一致直接失败，省得"改了基线忘了门面"。

用法（backend/ 目录）：
    python scripts/check_docs_numbers.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
README = REPO / "README.md"
BASELINE = REPO / "docs" / "README.md"

# (指标名, README 行定位词, 基线行定位词, 取值正则——按组比较)
RULES: list[tuple[str, str, str, str]] = [
    ("检索 MRR / Hit@1", "检索质量", "检索 MRR", r"(0\.\d+)\s*/\s*(0\.\d+)"),
    ("生成 Faithfulness / Relevancy", "生成质量", "生成 Faithfulness", r"(0\.\d+)\s*/\s*(1\.0)"),
    ("消融 CR", "消融", "消融 CR", r"(0\.\d+)\s*→\s*(0\.\d+)\s*→\s*\**\s*(0\.\d+)"),
    ("Agent route@1 / 拒绝", "Agent 编排", "Agent route@1", r"(1\.0)\s*/\s*(1\.0)"),
    ("性能 p50 / 吞吐", "性能", "检索 p50", r"(\d+)\s*ms\s*/\s*~?(\d+)\s*QPS"),
    ("Embedding Hit@1", "Embedding 选型", "Embedding Hit@1", r"(0\.\d+)"),
    (
        "测试规模（单测/集成/前端/E2E/task-agent）",
        "工程质量",
        "测试规模",
        r"单测[^\d]{0,20}(\d+)[^\d]{0,20}集成[^\d]{0,20}(\d+)[^\d]{0,20}前端[^\d]{0,20}(\d+)"
        r"[^\d]{0,20}E2E[^\d]{0,20}(\d+)[^\d]{0,20}task-agent[^\d]{0,20}(\d+)",
    ),
]


def _row(text: str, keyword: str) -> str:
    """取第一条含关键词的表格行（两篇文档的指标表都在最前面）。"""
    for line in text.splitlines():
        if line.lstrip().startswith("|") and keyword in line:
            return line
    raise SystemExit(f"文档数字检查失败：找不到含关键词「{keyword}」的表格行")


def main() -> int:
    readme = README.read_text(encoding="utf-8")
    baseline = BASELINE.read_text(encoding="utf-8")
    bad: list[str] = []
    for name, kw_readme, kw_baseline, pattern in RULES:
        hit_readme = re.search(pattern, _row(readme, kw_readme))
        hit_baseline = re.search(pattern, _row(baseline, kw_baseline))
        if not hit_readme or not hit_baseline:
            missing = "README" if not hit_readme else "docs/README"
            bad.append(f"{name}：{missing} 里没匹配到数字（格式可能被改动）")
            continue
        if hit_readme.groups() != hit_baseline.groups():
            bad.append(f"{name}：README={hit_readme.groups()} ≠ 基线={hit_baseline.groups()}")
    if bad:
        print("文档数字检查失败：README 与 docs/README 唯一基线不一致")
        for item in bad:
            print(f"  - {item}")
        print(f"\n请同步 README 与 {BASELINE.relative_to(REPO)}（两处必须一致）。")
        return 1
    print(f"文档数字检查通过：{len(RULES)} 项指标与唯一基线一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
