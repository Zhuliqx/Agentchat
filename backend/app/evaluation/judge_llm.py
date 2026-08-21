"""LLM-as-judge：评审模型与各指标 prompt（DeepSeek）。

设计决策：
- 固定 `deepseek-chat`（不用 reasoner：输出含思考链、慢且不稳定，不适合打分）。
- `temperature=0`：评审确定性、可复现——这是"可信评估"的前提（覆盖主对话
  settings.temperature=0.3）。
- `response_format={"type": "json_object"}`：DeepSeek 兼容 OpenAI JSON mode，
  简化解析；每个指标 prompt 显式给出输出字段。
- 独立实例：不进 `get_llm()` 缓存、不受运行时模型切换影响，与图缓存隔离。
- 局限（写入 docs/EVALUATION.md）：judge 与生成器同为 DeepSeek，可能整体高估
  （同源偏好）；指标绝对值仅供参考，A/B 相对变化更可信。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

JUDGE_MODEL = "deepseek-chat"  # 评审固定模型（DeepSeek 直连）
EVAL_TEMPERATURE = 0.0


def get_judge_llm() -> ChatOpenAI:
    """评审 LLM：确定性打分（独立实例，不缓存在主 LLM 工厂）。"""
    return ChatOpenAI(
        model=JUDGE_MODEL,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=EVAL_TEMPERATURE,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def get_eval_generator() -> ChatOpenAI:
    """评估用答案生成 LLM（与主对话同模型；temperature 0.2 贴近生产）。"""
    model = settings.deepseek_model or settings.llm_model or JUDGE_MODEL
    return ChatOpenAI(
        model=model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=0.2,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


# ---------------- prompt 构建（纯函数，便于单测） ----------------

_SYS_RULES = (
    "你是专业的 RAG 系统评审员。所有判断只依据给定材料，不允许臆测。\n"
    "严格遵守输出字段的类型约束，只输出合法 JSON 对象。"
)


def _format_docs(docs: list[dict], max_chars: int = 800) -> list[dict]:
    """把检索块截断为评审用的结构化列表（LLM 输入受限）。

    默认 800 字符：覆盖项目默认块长（chunk_size=800）下大多数块的关键信息；
    过小（如 400）会裁掉块后半段的关键句，造成 CR/Faithness 系统性低估
    （实证：q35 关键句位于第 448 字符，被 400 截断误判为未覆盖）。
    """
    return [
        {
            "index": i,
            "source": str(d.get("source") or ""),
            "text": str(d.get("text") or "")[:max_chars],
        }
        for i, d in enumerate(docs)
    ]


def _json_text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def build_precision_prompt(
    question: str, docs: list[dict]
) -> tuple[str, str]:
    """Context Precision：判定每个检索块是否与问题相关。"""
    sys = _SYS_RULES + (
        "\n\n任务：判断【检索块】中每一块是否真的与【问题】相关。"
        "\n输出严格 JSON：{\"relevant\": [true/false, ...]}，长度与检索块数量一致；"
        "true=该块内容对回答有帮助，false=不相关或噪声。"
    )
    user = (
        f"问题：{question}\n\n检索块：\n{_json_text(_format_docs(docs))}"
        "\n\n只输出 JSON。"
    )
    return sys, user


def build_recall_prompt(
    question: str, answer: str, docs: list[dict]
) -> tuple[str, str]:
    """Context Recall：从标准答案提取关键点，逐点判断是否被检索块覆盖。"""
    sys = _SYS_RULES + (
        "\n\n任务：1) 把【标准答案】拆分为若干关键信息点（如事实/数字，每点一句话）；"
        "\n2) 逐点判断该信息是否能在【检索块】中找到依据。"
        "\n输出严格 JSON："
        "{\"key_points\": [\"...\", ...], \"covered\": [true/false, ...]}，"
        "covered 长度与 key_points 一一对应。"
    )
    user = (
        f"问题：{question}\n\n标准答案：{answer}\n\n"
        f"检索块：\n{_json_text(_format_docs(docs))}\n\n只输出 JSON。"
    )
    return sys, user


def build_faithfulness_prompt(
    answer: str, docs: list[dict]
) -> tuple[str, str]:
    """Faithfulness：候选答案按句拆分，逐句判断是否可由检索块支撑（抓幻觉）。"""
    sys = _SYS_RULES + (
        "\n\n任务：把【候选答案】按句拆分（句号/叹号/问号/换行分句，剔除空句），"
        "逐句判断该句是否【完全/基本】可由【检索块】支撑。"
        "\n严格输出 JSON："
        "{\"sentences\": [\"句1\", ...], \"supported\": [true/false, ...]}。"
    )
    user = (
        f"候选答案：{answer}\n\n检索块：\n{_json_text(_format_docs(docs))}"
        "\n\n只输出 JSON。"
    )
    return sys, user


def build_ndcg_prompt(question: str, docs: list[dict]) -> tuple[str, str]:
    """NDCG 分级相关度：对每个检索块输出 0/1/2。

    2=直接命中/核心（能直接回答问题），1=部分相关/上下文，0=不相关。
    供 eval_rag --graded 模式计算 NDCG@K（排序质量指标）。
    """
    sys = _SYS_RULES + (
        "\n\n任务：对【检索块】中每一块，按与【问题】的相关程度打分级（graded relevance）："
        "\n- 2：直接命中/核心——该块本身就能回答问题（含关键事实/数字）。"
        "\n- 1：部分相关/上下文——与问题相关但仅提供背景或部分信息。"
        "\n- 0：不相关/噪声。"
        "\n输出严格 JSON：{\"relevance\": [0/1/2, ...]}，长度与检索块数量一致。"
    )
    user = (
        f"问题：{question}\n\n检索块：\n{_json_text(_format_docs(docs))}"
        "\n\n只输出 JSON。"
    )
    return sys, user


def build_relevancy_prompt(question: str, answer: str) -> tuple[str, str]:
    """Answer Relevancy：候选答案与问题的相关度（0-5，归一化 0-1）。

    评分指南针对对比型/筛选型/否定型问题给出明确指引，避免 judge 因
    "答案不是单一事实句"而系统性低估（实证：q31 对比、q37 筛选 Rel≈0.2）。
    """
    sys = _SYS_RULES + (
        "\n\n任务：评估【候选答案】与【问题】的相关度（是否直接回应问题的意图）。"
        "\n评分指南："
        "\n- 5：直接且完整地回应了问题。涵盖：给出具体事实/数字/对象；"
        "对比型问题（比较两者的异同）给出完整的对比结果；筛选型问题（哪个满足条件）"
        "给出满足条件的主体；否定型问题明确说明\u201c知识库中没有相关信息\u201d。"
        "\n- 3~4：切题但不够完整（只覆盖部分要点）。"
        "\n- 1~2：与问题沾边但答非所问/过于简略。"
        "\n- 0：完全跑题。"
        "\n注意：对于对比/筛选/否定型问题，只要答案回应了问题意图，不应因"
        "\u201c未给出单一数字/结论\u201d而降分。"
        "\n输出严格 JSON：{\"score\": 0-5 整数}。"
    )
    user = f"问题：{question}\n\n候选答案：{answer}\n\n只输出 JSON。"
    return sys, user


def build_generation_prompt(
    question: str, docs: list[dict]
) -> tuple[str, str]:
    """评估用答案生成（贴近 RAG prompt 的轻量实现，不依赖完整 graph）。

    v2 优化：区分「信息缺失」与「信息分散需要归纳推理」两种场景。
    对比/筛选/归纳类问题应综合多个检索块推理作答，而非一律说"没有信息"——
    实证：q31/q37 的 faithfulness 0.83 源于模型过度保守（拒绝跨块归纳）。
    """
    sys = (
        "你是严谨的知识库问答助手。规则：\n"
        "1. 仅基于给定的检索内容回答，不要编造事实；引用来源时标注来源文件。\n"
        "2. 若问题明确需要**对比、筛选或归纳**（如比较两个对象、判断哪个满足条件），"
        "可综合多个检索块中分散的信息进行推理作答，给出完整结论。\n"
        "3. 【否定保护】若检索内容**完全无法支撑**答案，必须明确说\"知识库中没有相关信息\"；"
        "禁止为凑出答案而把不同来源的信息强行拼凑（如把 A 产品规则套用到 B 产品）。\n"
        "4. 对普通事实型问题，直接给出检索块中的准确信息即可，不要额外扩展或添加来源注释。\n"
        "5. 用中文、条理清晰作答。"
    )
    user = (
        f"问题：{question}\n\n检索内容：\n{_json_text(_format_docs(docs, max_chars=600))}"
    )
    return sys, user


def jump_to_json(text: str | None) -> dict:
    """解析 LLM 输出的 JSON 对象：剥离代码围栏与前后杂文，失败返回 {}。"""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        start = 1 if lines and lines[0].startswith("```") else 0
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("```"):
                s = "\n".join(lines[start:i])
                break
        else:
            s = "\n".join(lines[start:])
    try:
        begin = s.index("{")
        end = s.rindex("}")
    except ValueError:
        return {}
    try:
        data = json.loads(s[begin : end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def judge(prompt_and_sys: tuple[str, str], retries: int = 1) -> dict:
    """执行一次评审调用（异步，可并发）；失败重试，最终失败返回 {}。"""
    sys, user = prompt_and_sys
    llm = get_judge_llm()
    for attempt in range(retries + 1):
        try:
            resp = await llm.ainvoke(
                [SystemMessage(content=sys), HumanMessage(content=user)]
            )
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            parsed = jump_to_json(text)
            if parsed:
                return parsed
            logger.warning("judge 输出非 JSON（第 %d 次）: %.120s", attempt + 1, text)
        except Exception as exc:  # noqa: BLE001 - 评审失败不应让整个评估崩掉
            logger.warning("judge 调用失败(第 %d 次): %s", attempt + 1, exc)
    return {}