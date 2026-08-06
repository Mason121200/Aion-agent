"""纯 Python 向量存储后端（numpy 实现，零 Rust / 零 chromadb 依赖）

MVP 说明：
- 内存余弦索引，数据全量保存在内存，增删改后同步落盘（全量写）
- 接口与 chromadb Collection 兼容：
  add(ids, embeddings, metadatas, documents)
  query(query_embedding, n_results, where) -> {"ids","distances","metadatas"}
  delete(ids) / update(...) / count() / peek()
- 持久化：numpy .npz + json（persist_dir 指定目录；None 为纯内存模式）
- 与 zero_code 的 NumpyVectorStore 逻辑一致，仅调整默认持久化目录
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

# 默认持久化目录：aion_agent/data/numpy_vector
_VECTOR_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "numpy_vector"
)


class NumpyVectorStore:
    """纯 numpy 内存向量索引（余弦相似度）"""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._ids: List[str] = []
        self._metadatas: List[Dict[str, Any]] = []
        self._documents: List[str] = []
        self._embeddings: Optional[np.ndarray] = None  # (N, dim) float32
        self._loaded = False
        self._load()

    # ==================== 持久化 ====================

    def _paths(self):
        return {
            "ids": self._persist_dir / "ids.json",
            "metas": self._persist_dir / "metadatas.json",
            "docs": self._persist_dir / "documents.json",
            "embs": self._persist_dir / "embeddings.npz",
        }

    def _load(self) -> None:
        """启动时从磁盘加载（幂等）；persist_dir=None 为纯内存模式"""
        if self._persist_dir is None:
            self._ids, self._metadatas = [], []
            self._documents, self._embeddings = [], None
            self._loaded = True
            return
        p = self._paths()
        try:
            if p["ids"].exists() and p["embs"].exists():
                self._ids = json.loads(p["ids"].read_text(encoding="utf-8"))
                self._metadatas = json.loads(
                    p["metas"].read_text(encoding="utf-8")
                ) if p["metas"].exists() else [{} for _ in self._ids]
                self._documents = json.loads(
                    p["docs"].read_text(encoding="utf-8")
                ) if p["docs"].exists() else ["" for _ in self._ids]
                with np.load(p["embs"], allow_pickle=False) as data:
                    self._embeddings = data["embeddings"].astype(np.float32)
                logger.info(
                    f"NumpyVectorStore 已加载 {len(self._ids)} 条向量 "
                    f"from {self._persist_dir}"
                )
            else:
                self._ids, self._metadatas = [], []
                self._documents, self._embeddings = [], None
        except Exception as e:  # 防御性：加载失败重建空索引
            logger.warning(f"NumpyVectorStore 加载失败，重建空索引: {e}")
            self._ids, self._metadatas = [], []
            self._documents, self._embeddings = [], None
        self._loaded = True

    def _save(self) -> None:
        """全量落盘（原子写：先写临时文件再替换）；persist_dir=None 跳过"""
        if self._persist_dir is None:
            return
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            p = self._paths()
            tmp_ids = p["ids"].with_suffix(".json.tmp")
            tmp_metas = p["metas"].with_suffix(".json.tmp")
            tmp_docs = p["docs"].with_suffix(".json.tmp")
            tmp_embs = p["embs"].with_name("embeddings.tmp.npz")
            tmp_ids.write_text(
                json.dumps(self._ids, ensure_ascii=False), encoding="utf-8"
            )
            tmp_metas.write_text(
                json.dumps(self._metadatas, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_docs.write_text(
                json.dumps(self._documents, ensure_ascii=False),
                encoding="utf-8",
            )
            if self._embeddings is not None:
                np.savez_compressed(tmp_embs, embeddings=self._embeddings)
                tmp_embs.replace(p["embs"])
            tmp_ids.replace(p["ids"])
            tmp_metas.replace(p["metas"])
            tmp_docs.replace(p["docs"])
        except Exception as e:  # 落盘失败不应中断主流程
            logger.error(f"NumpyVectorStore 持久化失败: {e}")

    # ==================== CRUD ====================

    def add(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        documents: List[str],
    ) -> None:
        """批量添加（去重：已存在的 id 走 upsert）"""
        if not ids:
            return
        new_embs = np.asarray(embeddings, dtype=np.float32)
        if new_embs.ndim != 2:
            raise ValueError("embeddings 必须是二维数组")
        for i, rid in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc = documents[i] if i < len(documents) else ""
            if rid in self._ids:
                # upsert
                idx = self._ids.index(rid)
                self._embeddings[idx] = new_embs[i]
                self._metadatas[idx] = meta
                self._documents[idx] = doc
            else:
                self._ids.append(rid)
                self._metadatas.append(meta)
                self._documents.append(doc)
                if self._embeddings is None:
                    self._embeddings = new_embs[i:i + 1]
                else:
                    self._embeddings = np.vstack(
                        [self._embeddings, new_embs[i:i + 1]]
                    )
        self._save()
        logger.debug(
            f"NumpyVectorStore 添加 {len(ids)} 条（当前 {len(self._ids)}）"
        )

    def query(
        self,
        query_embedding: List[float],
        n_results: int = 10,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """向量查询（余弦相似度 + where 等值过滤）

        Returns:
            {"ids": [[...]], "distances": [[...]], "metadatas": [[...]]}
        """
        empty = {"ids": [[]], "distances": [[]], "metadatas": [[]]}
        if not self._ids or self._embeddings is None:
            return empty
        q = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return empty
        q = q / q_norm

        # 先按 where 过滤索引
        idxs = list(range(len(self._ids)))
        if where:
            idxs = [
                i
                for i in idxs
                if all(
                    self._metadatas[i].get(k) == v for k, v in where.items()
                )
            ]
        if not idxs:
            return empty

        embs = self._embeddings[idxs]
        norms = np.linalg.norm(embs, axis=1)
        embs = embs / norms[:, None]

        # 余弦相似度（归一化后点积）
        scores = embs @ q.reshape(-1)
        # chromadb 返回 cosine distance = 1 - cosine_similarity
        distances = (1.0 - scores).astype(np.float64)

        k = min(n_results, len(idxs))
        if k <= 0:
            return empty
        top = np.argsort(distances)[:k]

        return {
            "ids": [[self._ids[idxs[i]] for i in top]],
            "distances": [[float(distances[i]) for i in top]],
            "metadatas": [[self._metadatas[idxs[i]] for i in top]],
        }

    def delete(self, ids: List[str]) -> None:
        """批量删除"""
        if not ids:
            return
        remove_set = set(ids)
        keep = [
            i for i in range(len(self._ids)) if self._ids[i] not in remove_set
        ]
        if len(keep) == len(self._ids):
            return
        self._ids = [self._ids[i] for i in keep]
        self._metadatas = [self._metadatas[i] for i in keep]
        self._documents = [self._documents[i] for i in keep]
        if self._embeddings is not None:
            self._embeddings = self._embeddings[keep]
        self._save()
        logger.debug(
            f"NumpyVectorStore 删除 {len(ids)} 条（当前 {len(self._ids)}）"
        )

    def update(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]],
        documents: List[str],
    ) -> None:
        """更新（upsert）"""
        self.add(ids, embeddings, metadatas, documents)

    def count(self) -> int:
        """文档总数"""
        return len(self._ids)

    def peek(self, limit: int = 10) -> Dict[str, Any]:
        """预览（接口兼容 chromadb collection.peek）"""
        n = min(limit, len(self._ids))
        return {
            "ids": self._ids[:n],
            "metadatas": self._metadatas[:n],
            "documents": self._documents[:n],
        }

    def reset(self) -> None:
        """清空索引（测试/迁移用）"""
        self._ids, self._metadatas = [], []
        self._documents, self._embeddings = [], None
        self._save()