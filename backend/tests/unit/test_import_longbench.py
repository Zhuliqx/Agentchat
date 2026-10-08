"""LongBench 导入器单测：文章/段落切分、答案探针与端到端小样本。"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.import_longbench import (
    answer_probe,
    best_matching_file,
    build,
    matching_files,
    sanitize,
    split_articles,
    split_paragraphs,
)


def test_split_articles():
    ctx = "文章1\n甲文内容\n\n文章2\n乙文内容"
    assert split_articles(ctx) == ["甲文内容", "乙文内容"]


def test_sanitize_removes_nul():
    assert sanitize("a\x00b") == "ab"


def test_split_paragraphs():
    ctx = "段落1：甲段\n\n段落2：乙段\n\n段落3：丙段"
    out = split_paragraphs(ctx)
    assert out["1"] == "甲段"
    assert out["3"] == "丙段"
    assert len(out) == 3


def test_answer_probe_strips_punct_and_min_len():
    assert answer_probe("厦门大学。") == "厦门大学"
    assert answer_probe("好。") == ""  # 太短，易误命中


def test_matching_files():
    texts = ["他在厦门大学任教", "无关内容"]
    paths = ["/a", "/b"]
    assert matching_files(texts, paths, ["厦门大学。"]) == ["/a"]


def test_best_matching_file_picks_longest_common_substring():
    answer = "该项目在2023年获得国家科技进步奖"
    texts = ["完全是别的新闻内容", "本报讯 该项目在2023年获得国家科技进步奖二等奖。"]
    paths = ["/a", "/b"]
    assert best_matching_file(texts, paths, [answer]) == "/b"


def test_best_matching_file_none_when_ambiguous_or_weak():
    answer = "该项目在2023年获得国家科技进步奖"
    texts = ["该项目在2023年获得国家科技进步奖", "该项目在2023年获得国家科技进步奖"]
    assert best_matching_file(texts, ["/a", "/b"], [answer]) is None
    assert best_matching_file(["短"], ["/a"], [answer]) is None


def test_build_end_to_end(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    rows = {
        "dureader": {
            "input": "在哪里任教",
            "context": "文章1\n他在厦门大学任教",
            "answers": ["厦门大学。"],
            "_id": "d1",
        },
        "multifieldqa_zh": {
            "input": "讲了什么",
            "context": "这是一篇很长的文档内容\x00带坏字符",
            "answers": ["某答案"],
            "_id": "m1",
        },
        "passage_retrieval_zh": {
            "input": "找目标段落",
            "context": "段落1：abc\n\n段落2：目标段落内容",
            "answers": ["段落2"],
            "_id": "p1",
        },
    }
    for task, row in rows.items():
        (src / f"{task}.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    out = tmp_path / "out"
    build(src, out, sample_size=10)
    for task in rows:
        gt = json.loads((out / f"longbench_{task}_gt.json").read_text(encoding="utf-8"))
        assert len(gt["cases"]) == 1, task
        expected = gt["cases"][0]["expected_sources"]
        assert expected and all(Path(p).exists() for p in expected)
        assert "\x00" not in Path(expected[0]).read_text(encoding="utf-8")
