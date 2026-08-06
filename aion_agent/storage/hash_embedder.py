"""HashEmbedder —— 离线确定性嵌入器（零外部模型）

用字符 n-gram 哈希把文本映射到固定维向量。
目的：让 NumpyVectorStore 在无 LLM / 无 embedding 模型时也能演示
完整的「文本 → 向量 → 余弦检索」链路。

生产替换：实现同样的 embed(text) -> List[float] 接口即可
（例如接入 OpenAI text-embedding-3-small 等真实模型）。
"""

from __future__ import annotations

import zlib
from typing import List

import numpy as np


class HashEmbedder:
    """字符 n-gram 哈希嵌入器

    - 确定性：同一文本永远得到同一向量（zlib.crc32，不受进程哈希盐影响）
    - 维度：默认 128 维，足够演示检索排序
    - 效果：字符重合度越高，余弦相似度越大（近似词面重叠的检索）
    """

    def __init__(self, dim: int = 128, ngram: int = 2):
        self.dim = dim
        self.ngram = ngram

    def embed(self, text: str) -> List[float]:
        text = (text or "").lower()
        vec = np.zeros(self.dim, dtype=np.float32)
        if not text:
            return vec.tolist()

        padded = "\x02" + text + "\x03"  # 首尾边界标记
        for i in range(len(padded) - self.ngram + 1):
            gram = padded[i:i + self.ngram]
            h = zlib.crc32(gram.encode("utf-8"))
            index = h % self.dim
            sign = 1.0 if (h & 1) else -1.0
            vec[index] += sign

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()

    @property
    def is_loaded(self) -> bool:
        return True