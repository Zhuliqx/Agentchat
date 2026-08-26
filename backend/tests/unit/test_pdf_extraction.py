"""PDF 提取（C1）单元测试：改用 pdfplumber/pymupdf 后中文表格无控制字符、表头保留。"""
from __future__ import annotations


def test_pdf_to_text_chinese_table_clean(tmp_path):
    import pymupdf
    from app.rag.ingestion import _pdf_to_text

    p = tmp_path / "t.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "测试科技员工信息表", fontsize=16, fontname="china-s")
    page.insert_text((72, 100), "部门 | 人数 | 占比", fontsize=11, fontname="china-s")
    doc.save(str(p))
    doc.close()

    text = _pdf_to_text(p)
    assert "部门" in text and "人数" in text
    # 不应含控制/空字符（pypdf 常见）
    assert not any(ch in text for ch in ("\x00", "\x0b"))


def test_pdf_to_text_fallback_empty_when_no_text(tmp_path):
    """无文本层 PDF（空白页）→ 安全返回空串，不抛。"""
    import pymupdf
    from app.rag.ingestion import _pdf_to_text

    p = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(p))
    doc.close()
    assert _pdf_to_text(p) == ""