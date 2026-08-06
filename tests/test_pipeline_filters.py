"""管道过滤器单元测试：MarkdownParseFilter + JsonParseFilter"""

import json

import pytest

from aion_agent.pipeline.json_parse_filter import JsonParseFilter
from aion_agent.pipeline.markdown_parse_filter import MarkdownParseFilter


@pytest.fixture
def md_filter():
    return MarkdownParseFilter()


class TestMarkdownParseFilter:
    """标记解析过滤器：流式状态机"""

    def test_visible_text_without_block(self, md_filter):
        visible, block = md_filter.feed("这是普通文本")
        assert visible == "这是普通文本"
        assert block is None

    def test_block_in_single_chunk(self, md_filter):
        text = (
            "回复内容"
            "<!--COGNITION_START-->"
            '[{"type":"triple","subject":"A","predicate":"B","object":"C"}]'
            "<!--COGNITION_END-->"
        )
        visible, block = md_filter.feed(text)
        assert visible == "回复内容"
        assert block is not None
        parsed = json.loads(block)
        assert len(parsed) == 1
        assert parsed[0]["subject"] == "A"

    def test_block_split_across_chunks(self, md_filter):
        """标记被切在块边界时仍能正确提取"""
        chunk1 = "回复内容<!--COGNITION_START-->[{"
        chunk2 = '"type":"triple","subject":"A","predicate":"B","object":"C"}]'
        chunk3 = "<!--COGNITION_END-->后续文本"

        v1, b1 = md_filter.feed(chunk1)
        assert "回复内容" in v1
        assert b1 is None

        v2, b2 = md_filter.feed(chunk2)
        assert v2 == ""
        assert b2 is None

        v3, b3 = md_filter.feed(chunk3)
        assert b3 is not None
        parsed = json.loads(b3)
        assert len(parsed) == 1

    def test_empty_cognition_block_skipped(self, md_filter):
        text = "回复内容<!--COGNITION_START-->[]<!--COGNITION_END-->"
        visible, block = md_filter.feed(text)
        assert visible == "回复内容"
        assert block is None

    def test_invalid_json_passed_to_downstream(self, md_filter):
        """非 JSON 内容交给下游过滤器，本过滤器不做格式校验"""
        text = "回复内容<!--COGNITION_START-->not json<!--COGNITION_END-->"
        visible, block = md_filter.feed(text)
        assert visible == "回复内容"
        assert block is not None
        assert "not json" in block

    def test_multiple_blocks(self, md_filter):
        c1 = (
            "<!--COGNITION_START-->"
            '[{"type":"triple","subject":"A","predicate":"B","object":"C","dimension":"user","confidence":0.9}]'
            "<!--COGNITION_END-->"
        )
        c2 = (
            "<!--COGNITION_START-->"
            '[{"type":"triple","subject":"X","predicate":"Y","object":"Z","dimension":"world","confidence":0.8}]'
            "<!--COGNITION_END-->"
        )
        text = "回复1" + c1 + "更多回复" + c2
        v, b = md_filter.feed(text)
        assert "回复1" in v
        assert "更多回复" in v
        assert b is not None
        parsed = json.loads(b)
        assert parsed[0]["subject"] == "X"

    def test_flush_incomplete_cognition(self, md_filter):
        visible, block = md_filter.feed("text<!--COGNITION_START-->[incomplete")
        assert visible == "text"
        assert block is None
        v2, b2 = md_filter.flush()
        assert v2 == ""
        assert b2 is not None
        assert "[incomplete" in b2

    def test_reset(self, md_filter):
        md_filter.feed("<!--COGNITION_START-->[{}]")
        md_filter.reset()
        assert md_filter._buffer == ""
        assert md_filter._in_cognition is False

    def test_partial_tag_prefix_not_output(self, md_filter):
        """标记前缀不能作为可见文本输出（防止被截断）"""
        visible, block = md_filter.feed("文本<!--COG")
        assert visible == "文本"
        assert block is None
        # 后续补全标记
        v2, b2 = md_filter.feed("NITION_START-->[]<!--COGNITION_END-->")
        assert v2 == ""
        assert b2 is None  # 空块静默丢弃


class TestJsonParseFilter:
    """JSON 解析过滤器"""

    @pytest.fixture
    def filter(self):
        return JsonParseFilter()

    def test_parse_valid_json(self, filter):
        raw = '[{"type":"triple","subject":"A","predicate":"B","object":"C"}]'
        result = filter.process(raw)
        assert len(result) == 1
        assert result[0]["subject"] == "A"

    def test_parse_empty_string(self, filter):
        assert filter.process("") == []
        assert filter.process(None) == []

    def test_parse_empty_array(self, filter):
        assert filter.process("[]") == []

    def test_parse_invalid_json(self, filter):
        assert filter.process("not json") == []

    def test_parse_not_array(self, filter):
        assert filter.process('{"key":"value"}') == []

    def test_parse_trailing_comma_repaired(self, filter):
        raw = (
            '[{"type":"triple","subject":"A"},'
            '{"type":"triple","subject":"B"},]'
        )
        result = filter.process(raw)
        assert len(result) == 2
        assert result[0]["subject"] == "A"
        assert result[1]["subject"] == "B"

    def test_parse_single_quotes(self, filter):
        raw = """[{'type': 'triple', 'subject': 'A', 'predicate': 'B', 'object': 'C'}]"""
        result = filter.process(raw)
        assert len(result) == 1
        assert result[0]["subject"] == "A"