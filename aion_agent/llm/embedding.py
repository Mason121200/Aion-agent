"""Embedding 服务 —— 可选 LLM 语义向量 / 离线 HashEmbedder 兜底

- LLMEmbeddingService：调用 OpenAI 兼容 /embeddings 接口（零第三方依赖）
- build_embedder()：按 AION_EMBEDDING 环境变量选择
    * 默认（hash）：HashEmbedder —— 离线确定性 n-gram 哈希（零依赖演示）
    * llm：LLMEmbeddingService —— 真实语义向量（需配置 API Key）

用途：语义去重（SemanticDedupFilter）与向量检索（NumpyVectorStore）共用。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from aion_agent.storage.hash_embedder import HashEmbedder

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "text-embedding-3-small"


class LLMEmbeddingService:
    """OpenAI 兼容 /embeddings 接口的嵌入服务（带文本缓存）"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = _DEFAULT_MODEL,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._cache: Dict[str, List[float]] = {}

    @property
    def is_loaded(self) -> bool:
        """配置了 API Key 即视为可用（首次调用失败会抛错由上层兜底）"""
        return bool(self.api_key)

    def _endpoint(self) -> str:
        return f"{self.base_url}/embeddings"

    def embed(self, text: str) -> List[float]:
        """单条文本 → 向量（带缓存）"""
        text = text or ""
        if text in self._cache:
            return self._cache[text]
        vectors = self.embed_many([text])
        self._cache[text] = vectors[0]
        return vectors[0]

    def embed_many(self, texts: List[str]) -> List[List[float]]:
        """批量文本 → 向量列表（保持输入顺序）"""
        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(
                f"Embedding 接口错误 {e.code}: {body}"
            ) from e

        items = data.get("data") or []
        by_index = {}
        for item in items:
            by_index[int(item.get("index", 0))] = list(
                item.get("embedding") or []
            )
        return [by_index.get(i, []) for i in range(len(texts))]


def build_embedder() -> Any:
    """按配置选择嵌入器（AION_EMBEDDING=llm → LLM 接口；否则离线哈希）"""
    mode = os.environ.get("AION_EMBEDDING", "hash").strip().lower()
    if mode == "llm":
        from aion_agent.llm.openai_compatible import get_config

        cfg = get_config()
        if cfg["api_key"]:
            model = os.environ.get("AION_EMBEDDING_MODEL", _DEFAULT_MODEL)
            logger.info(
                f"启用 LLM Embedding: model={model}, base={cfg['base_url']}"
            )
            return LLMEmbeddingService(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=model,
            )
        logger.warning("AION_EMBEDDING=llm 但未配置 API Key，回退离线 HashEmbedder")
    return HashEmbedder()
