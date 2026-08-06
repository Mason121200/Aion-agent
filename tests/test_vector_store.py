"""NumpyVectorStore 单元测试：余弦检索 + where 过滤 + 持久化"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from aion_agent.storage.numpy_vector_store import NumpyVectorStore

TEST_DIR = Path(__file__).resolve().parent.parent / "aion_agent" / "data" / "test_tmp"


@pytest.fixture
def store(tmp_path=None):
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    s = NumpyVectorStore(persist_dir=TEST_DIR)
    yield s
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def _vec(*values):
    """构造归一化测试向量"""
    v = np.asarray(values, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


class TestQuery:
    def test_rank_by_cosine_similarity(self, store):
        store.add(
            ids=["a", "b"],
            embeddings=[_vec(1, 0, 0), _vec(0.5, 0.5, 0)],
            metadatas=[{"user_id": "u1"}, {"user_id": "u1"}],
            documents=["doc a", "doc b"],
        )
        res = store.query(_vec(1, 0.1, 0), n_results=2)
        assert res["ids"][0] == ["a", "b"]
        assert res["distances"][0][0] < res["distances"][0][1]

    def test_where_filter(self, store):
        store.add(
            ids=["a", "b"],
            embeddings=[_vec(1, 0, 0), _vec(0, 1, 0)],
            metadatas=[{"user_id": "u1", "dimension": "world"},
                       {"user_id": "u2", "dimension": "world"}],
            documents=["", ""],
        )
        res = store.query(_vec(1, 0, 0), n_results=10, where={"user_id": "u1"})
        assert res["ids"][0] == ["a"]

    def test_empty_store(self, store):
        res = store.query(_vec(1, 0, 0), n_results=5)
        assert res["ids"] == [[]]

    def test_zero_query_vector(self, store):
        store.add(ids=["a"], embeddings=[_vec(1, 0, 0)], metadatas=[{}], documents=[""])
        res = store.query([0.0, 0.0, 0.0], n_results=5)
        assert res["ids"] == [[]]


class TestPersistence:
    def test_roundtrip(self):
        shutil.rmtree(TEST_DIR, ignore_errors=True)
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        try:
            s1 = NumpyVectorStore(persist_dir=TEST_DIR)
            s1.add(
                ids=["r1", "r2"],
                embeddings=[_vec(1, 0, 0), _vec(0, 1, 0)],
                metadatas=[{"user_id": "u1"}, {"user_id": "u1"}],
                documents=["中文文档一", "中文文档二"],
            )
            assert s1.count() == 2

            # 重新加载
            s2 = NumpyVectorStore(persist_dir=TEST_DIR)
            assert s2.count() == 2
            assert s2.peek()["documents"] == ["中文文档一", "中文文档二"]
            res = s2.query(_vec(1, 0.2, 0), n_results=1)
            assert res["ids"][0] == ["r1"]
        finally:
            shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_upsert_same_id(self, store):
        store.add(ids=["a"], embeddings=[_vec(1, 0, 0)], metadatas=[{"v": 1}], documents=["old"])
        store.add(ids=["a"], embeddings=[_vec(0, 1, 0)], metadatas=[{"v": 2}], documents=["new"])
        assert store.count() == 1
        assert store.peek()["documents"] == ["new"]
        res = store.query(_vec(0, 1, 0), n_results=1)
        assert res["ids"][0] == ["a"]

    def test_delete(self, store):
        store.add(
            ids=["a", "b"],
            embeddings=[_vec(1, 0, 0), _vec(0, 1, 0)],
            metadatas=[{}, {}],
            documents=["", ""],
        )
        store.delete(["a"])
        assert store.count() == 1
        assert store.peek()["ids"] == ["b"]

    def test_reset(self, store):
        store.add(ids=["a"], embeddings=[_vec(1, 0, 0)], metadatas=[{}], documents=[""])
        store.reset()
        assert store.count() == 0