"""反思验收环节测试：解析 / LLM 验收 / 更正格式化"""

import sys

sys.path.insert(0, ".")

from aion_agent.core.ports.i_llm_client import LLMResponse  # noqa: E402
from aion_agent.use_cases.react.verify import (  # noqa: E402
    _parse_verify_json,
    format_correction,
    verify_with_llm,
)


def run(coro):
    import asyncio
    return asyncio.run(coro)


class FakeVerifyLLM:
    def __init__(self, content):
        self.content = content

    async def complete(self, messages, **kwargs):
        return LLMResponse(content=self.content)


def test_parse_verify_json_variants():
    assert _parse_verify_json('{"verified": false, "issues": "虚构数据", "correction": "更正"}')["verified"] is False
    assert _parse_verify_json('```json\n{"verified": true, "issues": "", "correction": ""}\n```')["verified"] is True
    assert _parse_verify_json('前缀 {"verified": true} 后缀')["verified"] is True
    assert _parse_verify_json("没有 JSON") is None


def test_verify_skipped_without_tools():
    result = run(verify_with_llm(None, tool_results=[], final_reply="回答", turn=0))
    assert result["skipped"] is True
    assert result["verified"] is True


def test_verify_pass_and_fail():
    results = [{"tool_call_id": "c1", "content": "北京人口 2184 万", "success": True}]

    ok = run(verify_with_llm(
        FakeVerifyLLM('{"verified": true, "issues": "", "correction": ""}'),
        tool_results=results, final_reply="北京人口 2184 万", turn=0,
    ))
    assert ok["verified"] is True
    assert ok["skipped"] is False

    bad = run(verify_with_llm(
        FakeVerifyLLM('{"verified": false, "issues": "数据与工具结果不一致", "correction": "应为 2184 万"}'),
        tool_results=results, final_reply="北京人口 100 万", turn=0,
    ))
    assert bad["verified"] is False
    assert "2184" in bad["correction"]


def test_verify_parse_failure_defaults_pass():
    result = run(verify_with_llm(
        FakeVerifyLLM("我无法判断"),
        tool_results=[{"tool_call_id": "c1", "content": "x", "success": True}],
        final_reply="回答", turn=0,
    ))
    assert result["verified"] is True


def test_format_correction():
    assert format_correction("原回答", "更正内容") == "原回答\n\n---\n（更正）更正内容"
    assert format_correction("原回答", "") == "原回答"
