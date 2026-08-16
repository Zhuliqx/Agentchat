"""轻量 BM25 关键词检索（无第三方依赖）。

用于混合检索的关键词通道：与 Milvus 向量检索结果做 RRF 融合。
中文默认按单字符切分、英文按单词切分（不依赖 jieba，足够支撑关键词召回）；
配置 `BM25_USE_JIEBA=true` 且已安装 jieba 时，中文改用 jieba 分词，专名/术语召回更好。
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.config import settings

# 中文按单个汉字切分，英文/数字按单词切分
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")
# jieba 分词模式下用于过滤纯标点/空白 token
_TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+")

# 高频中文停用词（去除无检索价值的虚词/代词）
_STOPWORDS: frozenset[str] = frozenset(
    {
        "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一", "一个",
        "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
        "自己", "这", "那", "个", "与", "及", "或", "等", "并", "而", "但", "对", "从",
        "把", "被", "让", "向", "为", "以", "于", "之", "他", "她", "们", "我们", "你们",
        "他们", "什么", "怎么", "如何", "为什么", "因为", "所以", "如果", "可以", "能",
        "请", "帮", "一下", "吗", "呢", "啊", "吧", "嗯",
    }
)


def tokenize(text: str) -> list[str]:
    """切分为检索 token：英文单词 + 中文（单字或 jieba 分词），去除停用词。"""
    if settings.bm25_use_jieba:
        try:
            import jieba

            return [
                t
                for t in jieba.lcut(text.lower())
                if t.strip() and t not in _STOPWORDS and _TOKEN_RE.search(t)
            ]
        except ImportError:
            pass  # 未安装 jieba → 回退单字切分
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


class BM25Index:
    """Okapi BM25 索引。docs 为原始文本列表。"""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.n_docs = len(docs)
        self.doc_tokens: list[Counter] = [Counter(tokenize(d)) for d in docs]
        self.doc_lens: list[int] = [sum(c.values()) for c in self.doc_tokens]
        self.avgdl: float = sum(self.doc_lens) / max(self.n_docs, 1)

        self.df: dict[str, int] = {}
        for counter in self.doc_tokens:
            for token in counter:
                self.df[token] = self.df.get(token, 0) + 1

        # IDF：平滑版本，避免除零
        self.idf: dict[str, float] = {}
        for token, df in self.df.items():
            self.idf[token] = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def _score(self, counter: Counter, length: int, query_tokens: list[str]) -> float:
        score = 0.0
        denom_base = 1 - self.b + self.b * length / max(self.avgdl, 1e-9)
        for token in set(query_tokens):
            tf = counter.get(token, 0)
            if tf == 0:
                continue
            idf = self.idf.get(token, 0.0)
            score += idf * (tf * (self.k1 + 1)) / max(tf + self.k1 * denom_base, 1e-9)
        return score

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """返回 [(文档下标, BM25 得分)]，按得分降序，仅含得分 > 0 的命中。"""
        q_tokens = tokenize(query)
        if not q_tokens or self.n_docs == 0:
            return []
        scored = [
            (i, self._score(self.doc_tokens[i], self.doc_lens[i], q_tokens))
            for i in range(self.n_docs)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in scored if s > 0][:top_k]
