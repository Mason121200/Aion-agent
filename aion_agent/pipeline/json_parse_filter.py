"""过滤器2：JsonParseFilter — 解析 JSON 字符串为认知条目列表

管道-过滤器模式的第二道过滤器。
纯函数：同一个输入始终产生同一个输出。
"""

import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class JsonParseFilter:
    """JSON 解析过滤器 — 将标记块解析为结构化认知条目

    输入：<!--COGNITION--> 块内的原始 JSON 字符串
    输出：解析后的认知条目列表（list[dict]）

    安全策略：
    - 非 JSON 格式 → 返回空列表
    - 空数组 [] → 返回空列表
    - 非数组 JSON → 返回空列表
    - 部分损坏的 JSON → 尝试正则修复
    """

    def process(self, raw_json: str) -> List[Dict[str, Any]]:
        """解析 JSON 字符串为认知条目列表

        Args:
            raw_json: <!--COGNITION--> 块内的原始字符串

        Returns:
            解析后的认知条目列表，解析失败返回空列表
        """
        if not raw_json or not raw_json.strip():
            return []

        text = raw_json.strip()

        # 尝试直接解析
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            logger.warning(f"JSON 解析结果不是数组: {type(parsed)}")
            return []
        except json.JSONDecodeError:
            pass

        # 尝试修复常见问题后重新解析
        repaired = self._repair_json(text)
        if repaired:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass

        logger.warning(f"JSON 解析失败，无法修复: {text[:100]}...")
        return []

    def _repair_json(self, text: str) -> str:
        """修复常见 JSON 格式问题

        处理场景：
        1. 尾部逗号: [{"a":1},{"b":2},] → 去掉最后一个逗号
        2. 单引号代替双引号: {'a': 'b'} → 替换为双引号（内容无内嵌双引号时）
        """
        import re

        repaired = text

        # 修复尾部逗号（数组/对象最后一项后的逗号）
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

        # 单引号替换为双引号（只在内容外层）
        if "'" in repaired and '"' not in repaired:
            repaired = repaired.replace("'", '"')

        return repaired