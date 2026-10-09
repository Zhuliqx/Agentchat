"""CRUD-RAG 导入器：产物命名与文档名规则（1doc 单数、2/3docs 复数）。"""
from __future__ import annotations

from scripts.import_crud_rag import TASKS, _doc_name


def test_artifact_names_follow_upstream():
    assert TASKS["1docs"][3] == "1doc"
    assert TASKS["2docs"][3] == "2docs"
    assert TASKS["3docs"][3] == "3docs"


def test_doc_name_1doc_has_no_slot_suffix():
    assert _doc_name("1docs", "doc_1", 0) == "doc_1.txt"
    assert _doc_name("2docs", "doc_1", 1) == "doc_1__n2.txt"
