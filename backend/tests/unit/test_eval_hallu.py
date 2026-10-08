"""hallu 评测基础设施单测：字符 BLEU/ROUGE-L、探针、prompt 与导入器。"""
from __future__ import annotations

import json

import pytest

from app.evaluation import metrics
from app.evaluation.dataset import DatasetError
from app.evaluation.hallu import (
    bad_keyword_hits,
    extract_response,
    load_hallu_cases,
    make_probe,
    normalize_text,
)
from app.evaluation.judge_llm import build_hallu_fix_prompt, build_hallu_judge_prompt
from scripts.import_crud_hallu import build_cases, load_db_lines


def test_char_bleu_identity():
    assert metrics.char_bleu("李源潮在宁夏调研", "李源潮在宁夏调研", 1) == pytest.approx(1.0)
    assert metrics.char_bleu_avg("深入了解了群团工作", "深入了解了群团工作") == pytest.approx(1.0)


def test_char_bleu_partial_and_empty():
    assert metrics.char_bleu("", "参考文本", 1) == 0.0
    val = metrics.char_bleu("李源潮在宁夏调研", "李源潮在宁夏调研群团工作", 1)
    assert 0.0 < val < 1.0  # 候选更短 → 简短惩罚


def test_rouge_l_identity_and_disjoint():
    assert metrics.rouge_l_f1("了解了群团工作情况", "了解了群团工作情况") == pytest.approx(1.0)
    assert metrics.rouge_l_f1("abc", "完全不同内容") == pytest.approx(0.0)
    assert metrics.rouge_l_f1("", "参考") == 0.0


def test_rouge_l_partial_overlap():
    val = metrics.rouge_l_f1("李源潮在宁夏调研", "李源潮在宁夏调研时了解群团工作")
    assert 0.0 < val < 1.0


def test_normalize_and_probe():
    assert normalize_text("a\n  b\tc") == "a b c"
    assert make_probe("一二三四五六七八九十", length=4) == "一二三四"


def test_extract_response_prefers_tag():
    assert extract_response("解释文字 <response>正确改写</response> 尾巴") == "正确改写"
    assert extract_response("  没有标签  ") == "没有标签"


def test_bad_keyword_hits():
    assert bad_keyword_hits("李源潮与学员交流", ["学员", "宁夏群团学院"]) == ["学员"]
    assert bad_keyword_hits("", ["学员"]) == []


def test_load_hallu_cases_roundtrip(tmp_path):
    payload = {
        "cases": [
            {
                "id": "doc_1",
                "news_beginning": "开头",
                "hallucinated_continuation": "幻觉",
                "reference_corrected": "正确",
                "bad_keywords": ["虚构机构"],
                "remainder_probe": "正确续写",
            }
        ]
    }
    p = tmp_path / "gt.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cases = load_hallu_cases(p)
    assert len(cases) == 1
    assert cases[0].bad_keywords == ["虚构机构"]
    assert cases[0].remainder_probe == "正确续写"


def test_load_hallu_cases_missing_field(tmp_path):
    p = tmp_path / "gt.json"
    p.write_text(
        json.dumps({"cases": [{"id": "x", "news_beginning": "a"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(DatasetError):
        load_hallu_cases(p)


def test_build_cases_skips_bad_rows():
    rows = [
        {
            "ID": "ok",
            "newsBeginning": "开头",
            "hallucinatedContinuation": "幻觉",
            "hallucinatedMod": "正确",
            "newsRemainder": "正确的续写内容",
            "generatedBy": "test",
            "allKeywords": {"坏词": "不合理，没这回事", "好词": "合理"},
        },
        {
            "ID": "bad",
            "newsBeginning": "开头",
            "hallucinatedContinuation": "幻觉",
            "hallucinatedMod": '","msg":"request openai failed"',
        },
        {
            "ID": "noprobe",
            "newsBeginning": "开头",
            "hallucinatedContinuation": "幻觉",
            "hallucinatedMod": "正确",
            "newsRemainder": "不存在的续写",
        },
    ]
    cases, skipped = build_cases(rows, ["正确的续写内容在这里"])
    assert skipped == 2
    assert cases[0]["id"] == "ok"
    assert cases[0]["bad_keywords"] == ["坏词"]
    assert cases[0]["remainder_probe"] == "正确的续写内容"


def test_load_db_lines(tmp_path):
    d = tmp_path / "hallu_docs"
    d.mkdir()
    for i, text in ((1, "第一行\n\n"), (2, "第二行\n"), (3, "第三行\n")):
        (d / f"documents_hallu.txt_part_{i}").write_text(text, encoding="utf-8")
    assert load_db_lines(tmp_path) == ["第一行", "第二行", "第三行"]


def test_hallu_prompts_contain_inputs():
    sys_p, user_p = build_hallu_fix_prompt(
        "开头", "幻觉续写", [{"source": "s", "text": "材料"}]
    )
    assert "<response>" in user_p
    assert "幻觉续写" in user_p and "材料" in user_p
    jsys, juser = build_hallu_judge_prompt(
        "开头", "幻觉续写", "参考修订", "候选修订", docs=[{"source": "s", "text": "铁证材料"}]
    )
    assert "fixes_hallucination" in jsys
    assert "参考修订" in juser and "候选修订" in juser
    assert "铁证材料" in juser
