"""抓取中文维基公司词条正文（API extract，无模板/脚注噪声）并清洗为知识库文档。

用途：扩充 RAG 知识库样本，造出"多家同主题实体"使来源级召回饱和度下降，便于重测 RAG 三档。
仅生成文本文件，不摄入（摄入方案另行确认）。

用法：python scripts/fetch_kb_docs.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent  # 项目根（Agentchat/）
OUT = ROOT / "data" / "kb_expand"

# 语义相关的 AI/大模型/软件公司（中文维基词条，实体清晰、可判定）
TITLES = [
    "商汤科技",
    "科大讯飞",
    "旷视科技",
    "云从科技",
    "月之暗面 (公司)",
    "智谱",
    "依图科技",
    "格灵深瞳",
    "MiniMax",
    # ———— 新增：互联网 / 大厂（扩大来源、制造歧义）————
    "阿里巴巴集团",
    "腾讯",
    "字节跳动",
    "百度",
    "京东",
    "美团",
    "网易",
    # ———— 新增：硬科技 / 制造（噪声/干扰源）————
    "中芯国际",
    "宁德时代",
    "比亚迪",
    "华为",
    # ———— 新增：传统 / 其他行业（干扰源）————
    "用友网络",
    "迈瑞医疗",
    "贵州茅台",
    "招商银行",
]

# 尾部模板标题（之后的内容基本是空引用，截断删除）
_TAIL_MARKS = ("参考文献", "參考料", "參考資料", "参考来源", "参考资料", "外部链接", "外部連結", "扩展阅读")


def fetch_extract(title: str) -> str:
    """调维基 API 取该词条纯文本正文（explaintext，自动跟随重定向）。"""
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "format": "json",
        "titles": title,
    }
    url = f"https://zh.wikipedia.org/w/api.php?{requests.compat.urlencode(params)}"
    resp = requests.get(url, headers={"User-Agent": "agentchat-kb-expand/1.0"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    # 找非 missing 页
    for pid, page in pages.items():
        if "missing" in page:
            continue
        return page.get("extract", "") or ""
    return ""


def clean(text: str) -> str:
    """去掉正文尾部模板标题与多余空行，去掉重定向/消歧模板说明。"""
    if not text:
        return ""
    # 去掉"簡繁重定向/消歧"类说明段
    text = re.sub(r"^簡繁重定向：.*?(?=\n+==|\Z)", "", text, flags=re.S)
    text = re.sub(r"^月之暗面可能指：.*?(?=\n+==|\Z)", "", text, flags=re.S)
    # 截断到第一个尾部模板标题（其后无正文）
    idx = len(text)
    for mark in _TAIL_MARKS:
        m = re.search(rf"^==\s*{re.escape(mark)}.*$", text, flags=re.M)
        if m:
            idx = min(idx, m.start())
    text = text[:idx]
    # 清理孤立的空标题与过多空行
    text = re.sub(r"^==\s*[^=]*\s*==\s*$", "", text, flags=re.M)  # 去掉空标题行（内容在下文已保留）
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for title in TITLES:
        raw = fetch_extract(title)
        text = clean(raw)
        if not text:
            print(f"  [空] {title}")
            continue
        fname = title.split()[0].replace("/", "_") + ".txt"
        fpath = OUT / fname
        fpath.write_text("# " + title + "\n\n" + text + "\n", encoding="utf-8")
        print(f"  [ok] {title}  -> {fname}  ({len(text)} 字符)")
        ok += 1
    print(f"\n完成 {ok}/{len(TITLES)}；输出目录 {OUT}")


if __name__ == "__main__":
    main()
