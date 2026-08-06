"""过滤器1：MarkdownParseFilter — 从 LLM 流中实时提取 <!--COGNITION--> 块

管道-过滤器模式的第一道过滤器。
使用状态机 + 部分匹配缓冲，支持任意大小的流式输入块。

职责边界：
- 本过滤器只做「标记提取」——找到 <!--COGNITION_START--> 和 <!--COGNITION_END--> 标记
- 提取标记间的原始内容，不做 JSON/格式校验
- 格式校验由下游过滤器（JsonParseFilter）负责
- 仅对空内容或 [] 做静默丢弃
"""

import re
from typing import Tuple, Optional

_START_TAG = "<!--COGNITION_START-->"
_END_TAG = "<!--COGNITION_END-->"


class MarkdownParseFilter:
    """标记解析过滤器 — 从流文本中提取认知标记块

    状态机实现，将流式文本实时分离为：
    - 可见文本（LLM 回复的主体）
    - 认知标记块（<!--COGNITION_START--> ... <!--COGNITION_END-->）

    安全策略（防误提取）：
    - 只提取完全配对的开始/结束标记之间的内容
    - 空内容或 [] → 静默丢弃
    - 不做 JSON 格式校验（由下游过滤器负责）
    """

    def __init__(self):
        self._buffer = ""
        self._in_cognition = False
        self._cognition_content = ""
        self._start_pattern = re.compile(
            r"<!--\s*COGNITION_START\s*-->", re.IGNORECASE
        )
        self._end_pattern = re.compile(
            r"<!--\s*COGNITION_END\s*-->", re.IGNORECASE
        )

    def reset(self) -> None:
        """重置状态机"""
        self._buffer = ""
        self._in_cognition = False
        self._cognition_content = ""

    def process(self, chunk: str) -> Tuple[str, Optional[str]]:
        """处理一个文本块（feed 的别名，语义更清晰）

        Returns:
            (visible_text, cognition_block_or_None)
        """
        return self.feed(chunk)

    def feed(self, chunk: str) -> Tuple[str, Optional[str]]:
        """输入一个文本块

        Returns:
            (visible_text, cognition_block_or_None)
        """
        if not chunk:
            return "", None

        self._buffer += chunk
        visible_parts = []
        completed_block = None

        while self._buffer:
            if not self._in_cognition:
                # 查找开始标记
                match = self._start_pattern.search(self._buffer)
                if match:
                    before = self._buffer[:match.start()]
                    if before:
                        visible_parts.append(before)
                    self._buffer = self._buffer[match.end():]
                    self._in_cognition = True
                    self._cognition_content = ""
                else:
                    # 还没到开始标记：安全截断后输出可见文本
                    # 防止标记被截断（如 buffer 结尾是 <!--COG）
                    safe_len = self._safe_output_point(
                        self._buffer, _START_TAG
                    )
                    if safe_len > 0:
                        visible_parts.append(self._buffer[:safe_len])
                        self._buffer = self._buffer[safe_len:]
                    else:
                        break
            else:
                # 在认知块内，查找结束标记
                end_match = self._end_pattern.search(self._buffer)
                if end_match:
                    self._cognition_content += self._buffer[:end_match.start()]
                    result = self._extract_cognition_block(
                        self._cognition_content.strip(),
                        visible_parts,
                    )
                    if result:
                        completed_block = result
                    self._buffer = self._buffer[end_match.end():]
                    self._in_cognition = False
                    self._cognition_content = ""
                else:
                    # 还没到结束标记，安全截断（避免标记被截断）
                    split = self._safe_split_in_cognition(self._buffer)
                    self._cognition_content += self._buffer[:split]
                    self._buffer = self._buffer[split:]
                    break

        return "".join(visible_parts), completed_block

    def flush(self) -> Tuple[str, Optional[str]]:
        """冲刷缓冲区，返回 (可见残留, 认知块或 None)"""
        visible = ""
        cognition_block = None

        if self._in_cognition:
            raw = self._cognition_content.strip()
            if raw and raw != "[]":
                cognition_block = raw
            # raw == "[]" → 空列表静默丢弃

        if self._buffer.strip():
            visible += self._buffer

        self.reset()
        return visible, cognition_block

    def _extract_cognition_block(
        self, raw: str, visible_parts: list
    ) -> Optional[str]:
        """提取认知块内容（不做 JSON 格式校验）

        - 空内容 → None（静默丢弃）
        - [] → None（静默丢弃）
        - 其他内容 → 返回原始字符串（由下游过滤器解析）
        """
        if not raw:
            return None
        if raw == "[]":
            return None
        return raw

    def _safe_output_point(self, buffer: str, tag: str) -> int:
        """找到安全的输出截断点

        不将可能形成标记开头的内容输出，防止标记被截断。
        """
        for suffix_len in range(len(buffer), 0, -1):
            suffix = buffer[-suffix_len:]
            if tag.startswith(suffix):
                return len(buffer) - suffix_len
        return len(buffer)

    def _safe_split_in_cognition(self, buffer: str) -> int:
        """在认知块内查找安全截断点

        防止结束标记被提前输出：找到第一个是 _END_TAG 前缀的后缀，
        将该后缀之前的内容安全保留。
        """
        for suffix_len in range(len(buffer), 0, -1):
            suffix = buffer[-suffix_len:]
            if _END_TAG.startswith(suffix):
                return len(buffer) - suffix_len
        return len(buffer)