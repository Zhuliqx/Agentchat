"""XFUND 导入器单测：键值对解析、图片转 PDF 与端到端小样本。"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.import_xfund import build, build_pairs, image_to_pdf


def test_build_pairs_links_question_to_answer():
    document = [
        {"id": 1, "text": "姓名:", "label": "question", "linking": [[1, 2]]},
        {"id": 2, "text": "夏艳辰", "label": "answer", "linking": []},
        {"id": 3, "text": "页眉", "label": "header", "linking": []},
    ]
    assert build_pairs(document) == [("姓名:", "夏艳辰")]


def test_build_pairs_skips_empty_and_dupes():
    document = [
        {"id": 1, "text": "姓名:", "label": "question", "linking": [[1, 2], [1, 2]]},
        {"id": 2, "text": "夏艳辰", "label": "answer", "linking": []},
        {"id": 3, "text": "  ", "label": "question", "linking": [[3, 4]]},
        {"id": 4, "text": "", "label": "answer", "linking": []},
    ]
    assert build_pairs(document) == [("姓名:", "夏艳辰")]


def test_image_to_pdf(tmp_path):
    img_path = tmp_path / "a.jpg"
    Image.new("RGB", (60, 40), "white").save(img_path)
    pdf_path = tmp_path / "a.pdf"
    image_to_pdf(img_path, pdf_path)
    assert pdf_path.exists() and pdf_path.stat().st_size > 0


def test_build_end_to_end(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (60, 40), "white").save(images / "zh_val_0.jpg")
    payload = {
        "documents": [
            {
                "uid": "zh_val_0",
                "img": {"fname": "zh_val_0.jpg"},
                "document": [
                    {"id": 1, "text": "姓名:", "label": "question", "linking": [[1, 2]]},
                    {"id": 2, "text": "夏艳辰", "label": "answer", "linking": []},
                ],
            }
        ]
    }
    json_path = tmp_path / "zh.val.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    build(json_path, images, out)
    gt = json.loads((out / "xfund_zh_gt.json").read_text(encoding="utf-8"))
    assert len(gt["cases"]) == 1
    case = gt["cases"][0]
    assert case["expected_images"][0].endswith("#0")
    assert Path(case["expected_images"][0][:-2]).exists()
    title_gt = json.loads((out / "xfund_zh_title_gt.json").read_text(encoding="utf-8"))
    assert len(title_gt["cases"]) == 1
    assert title_gt["cases"][0]["question"] == "姓名:"
