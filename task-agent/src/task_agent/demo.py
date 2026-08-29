"""离线可运行 demo（无需 API key，脚本化 LLM）。

用法：
    python -m task_agent.demo                          # 离线 FakeLLM
    TASK_AGENT_OPENAI_API_KEY=xxx python -m task_agent.demo  # 真实 OpenAI 兼容端点
"""
from __future__ import annotations

import asyncio
import os
import sys
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from task_agent.config import TaskAgentConfig
from task_agent.graph import build_agent
from task_agent.llm import LLM

DEMO_GOAL = "介绍一下公司（知识库）并计算 1 到 100 所有质数的和"


class _ScriptedLLM:
    """按提示词特征返回脚本化 JSON 的假 LLM（确定性、可离线复现）。"""

    def __init__(self) -> None:
        self._replan_calls = 0
        self._check_calls = 0

    async def ainvoke(self, prompt: str) -> Any:
        if "可用信息来源" in prompt:  # REPLAN_PROMPT 特征
            self._replan_calls += 1
            if self._replan_calls == 1:
                return SimpleNamespace(
                    content='{"next_action": "查询知识库中公司的成立年份", "expected_source": "kb"}'
                )
            return SimpleNamespace(
                content='{"next_action": "计算 1 到 100 所有质数的和", "expected_source": "code"}'
            )
        if "完成度检查员" in prompt:  # CHECK_PROMPT 特征
            self._check_calls += 1
            return SimpleNamespace(
                content='{"done": false}'
                if self._check_calls == 1
                else '{"done": true}'
            )
        if "结果整合器" in prompt:  # FINAL_PROMPT 特征
            return SimpleNamespace(
                content="已完成目标：公司成立于 2020 年；1 到 100 质数和为 1060。"
            )
        if "可用工具" in prompt:  # TOOLCALL_PROMPT 特征 → 直接回答
            return SimpleNamespace(
                content='{"answer": "根据知识库，公司成立于 2020 年；质数和为 1060。"}'
            )
        # 执行步（DefaultExecutor 直答）
        return SimpleNamespace(content="根据知识库，公司成立于 2020 年；质数和为 1060。")


class _OpenAICompatLLM:
    """可选：配置了 TASK_AGENT_OPENAI_API_KEY 时使用真实 OpenAI 兼容端点。"""

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=os.environ.get("TASK_AGENT_OPENAI_API_KEY"),
            base_url=os.environ.get("TASK_AGENT_OPENAI_BASE_URL")
            or "https://api.deepseek.com",
        )
        self._model = os.environ.get("TASK_AGENT_OPENAI_MODEL") or "deepseek-chat"

    async def ainvoke(self, prompt: str) -> Any:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return SimpleNamespace(content=resp.choices[0].message.content or "")


@lru_cache(maxsize=1)
def _openai_llm() -> _OpenAICompatLLM:
    return _OpenAICompatLLM()


async def run_demo_flow(
    goal: str = DEMO_GOAL, on_event=None
) -> dict:
    """跑一遍完整 replan 流程（无 checkpointer → 无 HITL）。返回图执行结果。"""
    scripted = _ScriptedLLM()

    def _factory() -> LLM:
        if os.environ.get("TASK_AGENT_OPENAI_API_KEY"):
            return _openai_llm()
        return scripted

    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False),
        llm_factory=_factory,
        checkpointer_provider=lambda: None,
        on_event=on_event,
    )
    return await agent.ainvoke({"goal": goal})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("== task-agent demo（自主任务 Agent）==")
    print(f"目标: {DEMO_GOAL}")
    if os.environ.get("TASK_AGENT_OPENAI_API_KEY"):
        print("LLM: OpenAI 兼容端点（TASK_AGENT_OPENAI_*）")
    else:
        print("LLM: 脚本化 FakeLLM（离线，无需 key）")
    def _on_event(kind: str, data: dict) -> None:
        payload = " ".join(f"{k}={v}" for k, v in data.items())
        print(f"  [event] {kind}: {payload}")

    print("\n执行过程：")
    result = asyncio.run(run_demo_flow(on_event=_on_event))
    print("\nfindings:")
    for i, f in enumerate(result.get("findings") or [], 1):
        print(f"  [{i}] {f}")
    print("\nfinal_answer:", result.get("final_answer"))


if __name__ == "__main__":
    main()
