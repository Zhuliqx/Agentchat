"""评估子系统单元测试（纯逻辑，不依赖 Postgres/Milvus/LLM）。

覆盖：四指标计算、LLM-judge prompt 与 JSON 解析、ground truth 校验、
eval_rag.py 的命中判定 helper。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.evaluation import dataset, metrics
from app.evaluation.judge_llm import (
    build_faithfulness_prompt,
    build_generation_prompt,
    build_precision_prompt,
    build_recall_prompt,
    build_relevancy_prompt,
    jump_to_json,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------- metrics ----------------

def test_context_precision_position_weighted():
    # [True, False, True]: 1/1 + 2/3 = 1.6667，除以相关数 2 → 0.8333
    assert round(metrics.context_precision([True, False, True]), 4) == 0.8333


def test_context_precision_empty_and_no_hits():
    assert metrics.context_precision([]) == 0.0
    assert metrics.context_precision([False, False]) == 0.0


def test_context_recall_and_faithfulness():
    assert round(metrics.context_recall([True, False, True]), 4) == 0.6667
    assert metrics.context_recall([]) == 0.0
    assert metrics.faithfulness([True, True, True]) == 1.0
    assert metrics.faithfulness([]) == 0.0


def test_answer_relevancy_normalization():
    assert metrics.answer_relevancy(5) == 1.0
    assert metrics.answer_relevancy(4) == 0.8
    assert metrics.answer_relevancy(0) == 0.0
    assert metrics.answer_relevancy(None) == 0.0
    assert metrics.answer_relevancy("oops") == 0.0
    assert metrics.answer_relevancy(7) == 1.0  # 越界被 clamp


def test_safe_bool_list():
    assert metrics.safe_bool_list(["true", "no", "1"], 3) == [True, False, True]
    assert metrics.safe_bool_list("bad", 3) == [False, False, False]
    assert metrics.safe_bool_list([True], 3) == [True, False, False]  # 补齐


def test_macro_average():
    assert metrics.macro_average([1.0, 0.5, 0.0]) == 0.5
    assert metrics.macro_average([]) == 0.0


# ---------------- NDCG（排序质量） ----------------

def test_ndcg_perfect_and_worst():
    # 完美排序：2,1,1 -> DCG=IDCG -> NDCG=1.0
    assert round(metrics.ndcg_at_k([2, 1, 1], 3), 4) == 1.0
    # 无相关 -> 0
    assert metrics.ndcg_at_k([0, 0, 0], 3) == 0.0
    # 空输入 -> 0
    assert metrics.ndcg_at_k([], 3) == 0.0


def test_ndcg_penalizes_order():
    # 核心块排第 3 vs 排第 1，NDCG@3 应更低
    good = metrics.ndcg_at_k([2, 1, 1], 3)
    bad = metrics.ndcg_at_k([1, 1, 2], 3)
    assert good > bad


def test_ndcg_equals_mrr_when_single_core():
    # 只有一个 2 分核心块：NDCG@1 与 Hit@1 语义一致
    assert metrics.ndcg_at_k([2, 0, 0], 1) == 1.0
    assert metrics.ndcg_at_k([0, 2, 0], 1) == 0.0


def test_safe_graded_list():
    assert metrics.safe_graded_list(["2", "1", "0"], 3) == [2, 1, 0]
    assert metrics.safe_graded_list([True, False], 3) == [2, 0, 0]
    assert metrics.safe_graded_list("bad", 2) == [0, 0]
    assert metrics.safe_graded_list([9, 1], 2) == [2, 1]  # 越界 clamp 到 2


# ---------------- judge prompt & JSON 解析 ----------------

def test_jump_to_json_variants():
    assert jump_to_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert jump_to_json('前缀 {"x": [true]} 后缀') == {"x": [True]}
    assert jump_to_json("不是 JSON") == {}
    assert jump_to_json("") == {}
    assert jump_to_json("{" + '"broken": ') == {}


def test_prompts_contain_required_fields():
    docs = [{"text": "公司有 120 名员工", "source": "company.md"}]
    assert "relevant" in build_precision_prompt("q", docs)[0]
    assert "key_points" in build_recall_prompt("q", "标准答案", docs)[0]
    assert "sentences" in build_faithfulness_prompt("答案", docs)[0]
    assert "score" in build_relevancy_prompt("q", "答案")[0]
    assert "对比型" in build_relevancy_prompt("q", "答案")[0]  # 对比/筛选/否定型指引
    assert "检索" in build_generation_prompt("q", docs)[1]


# ---------------- ground truth ----------------

def test_load_ground_truth_example():
    cases = dataset.load_ground_truth(FIXTURES / "rag_ground_truth.example.json")
    assert len(cases) == 2
    assert cases[0].question
    assert isinstance(cases[0].expected_sources, list)


def test_load_ground_truth_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"cases": [{"id": "a"}]}', encoding="utf-8")  # 缺 question
    with pytest.raises(dataset.DatasetError):
        dataset.load_ground_truth(bad)

    dup = tmp_path / "dup.json"
    dup.write_text(
        '{"cases": [{"id": "a", "question": "q1"}, {"id": "a", "question": "q2"}]}',
        encoding="utf-8",
    )
    with pytest.raises(dataset.DatasetError):
        dataset.load_ground_truth(dup)

    with pytest.raises(dataset.DatasetError):
        dataset.load_ground_truth(tmp_path / "missing.json")


# ---------------- eval_rag helpers ----------------

def test_eval_rag_hit_logic():
    sys.path.insert(0, str(SCRIPTS))
    import eval_rag  # noqa: E402

    assert eval_rag._hit("keywords", ["员工"], "a.md", "公司有 120 名员工") is True
    assert eval_rag._hit("keywords", ["不存在"], "a.md", "其他内容") is False
    assert eval_rag._hit("source", ["company.md"], "C:/uploads/company.md", "") is True
    assert eval_rag._hit("source", ["other.md"], "C:/uploads/company.md", "") is False
    assert eval_rag._hit("keywords", [], "a.md", "任何") is False