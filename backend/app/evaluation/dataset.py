"""ground truth 数据集加载与校验。

格式（data/eval/ground_truth.json）：
{
  "name": "agentchat-rag-benchmark-v1",
  "note": "人工标注，仅基于已上传的真实知识库文档",
  "cases": [
    {
      "id": "q01",
      "question": "公司有多少名员工?",
      "answer": "公司现有约 120 名员工。",     // 标准答案(生成质量/召回的参照)
      "expected_sources": [".../company.md"],  // 期望命中的文档(检索级校验)
      "expected_images": [".../c.md#2"],       // 可选:期望命中的图片 id(图文双通道; 与 sources 是"或"关系)
      "notes": ""                              // 可空:标注注意点/困难点
    }
  ]
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class DatasetError(ValueError):
    """ground truth 数据非法。"""


@dataclass
class Case:
    id: str
    question: str
    answer: str = ""
    expected_sources: list[str] = field(default_factory=list)
    # 图片/图文双通道命中的图片 id（格式 source#image_index，如 "D:\\...\\c.md#2"）。
    # 与 expected_sources 是"或"的关系：命中任一即视为该 case 命中。
    expected_images: list[str] = field(default_factory=list)
    notes: str = ""

    def to_doc_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "expected_sources": self.expected_sources,
            "expected_images": self.expected_images,
            "notes": self.notes,
        }


def _clean_sources(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for s in raw:
        if isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


def load_ground_truth(path: str | Path) -> list[Case]:
    """读取并校验 GT 文件，返回案例列表。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatasetError(f"ground truth 不存在: {p}") from None
    except json.JSONDecodeError as exc:
        raise DatasetError(f"ground truth JSON 解析失败: {exc}") from None

    cases: list[Case] = []
    seen: set[str] = set()
    raw_cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError(f"ground truth 缺少非空 cases 列表: {p}")

    for i, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            raise DatasetError(f"cases[{i}] 不是对象")
        cid = str(raw.get("id") or f"case_{i:02d}").strip()
        question = str(raw.get("question") or "").strip()
        answer = str(raw.get("answer") or "").strip()
        if not question:
            raise DatasetError(f"case {cid} 缺少 question")
        if cid in seen:
            raise DatasetError(f"case id 重复: {cid}")
        seen.add(cid)
        cases.append(
            Case(
                id=cid,
                question=question,
                answer=answer,
                expected_sources=_clean_sources(raw.get("expected_sources")),
                expected_images=_clean_sources(raw.get("expected_images")),
                notes=str(raw.get("notes") or ""),
            )
        )
    return cases


def save_ground_truth(path: str | Path, cases: list[Case], name: str = "") -> None:
    """把案例持久化为 GT 文件（用于模板生成/扩展）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name or "agentchat-rag-benchmark",
        "note": "人工标注，仅基于真实知识库文档；来源务必在 notes 注明",
        "cases": [c.to_doc_dict() for c in cases],
    }
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )