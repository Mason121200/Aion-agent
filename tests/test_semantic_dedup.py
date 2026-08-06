"""语义去重过滤器测试"""

from aion_agent.pipeline.semantic_dedup_filter import SemanticDedupFilter


class _FakeEmbedder:
    """受控向量：相同文本→同一单位向量（余弦=1）；不同文本→正交（余弦=0）"""

    def __init__(self):
        self.is_loaded = True
        self._index = {}
        self._dim = 32

    def embed(self, text):
        if text not in self._index:
            self._index[text] = len(self._index) % self._dim
        slot = self._index[text]
        v = [0.0] * self._dim
        v[slot] = 1.0
        return v


def _item(subject, predicate, obj):
    return {"subject": subject, "predicate": predicate, "object": obj}


def test_exact_dedup_keeps_first():
    f = SemanticDedupFilter()
    items = [
        _item("小杨", "喜欢", "看电影"),
        _item("小杨", "喜欢", "看电影"),
        _item("小杨", "喜欢", "爬山"),
    ]
    out = f.process(items)
    assert len(out) == 2
    assert out[0]["object"] == "看电影"


def test_missing_fields_kept():
    f = SemanticDedupFilter()
    items = [
        {"subject": "小杨"},  # 无 predicate/object → 不参与去重
        _item("a", "b", "c"),
        _item("a", "b", "c"),
    ]
    out = f.process(items)
    assert len(out) == 2


def test_cross_batch_dedup():
    f = SemanticDedupFilter()
    existing = [_item("小杨", "喜欢", "看电影")]
    out = f.process([_item("小杨", "喜欢", "看电影"), _item("B", "C", "D")], existing)
    assert len(out) == 1
    assert out[0]["subject"] == "B"


def test_semantic_dedup_identical_removed():
    f = SemanticDedupFilter(embedder=_FakeEmbedder())
    items = [
        _item("小杨", "喜欢", "看电影"),
        _item("小杨", "喜欢", "看电影"),  # 完全同义 → 语义去重移除
        _item("项目", "使用", "numpy"),
    ]
    out = f.process(items)
    assert len(out) == 2
    assert out[0]["object"] == "看电影"
    assert out[1]["object"] == "numpy"


def test_semantic_dedup_disabled_without_embedder():
    """无 embedder 时只做精确去重，不做语义去重"""
    f = SemanticDedupFilter()
    items = [
        _item("小杨", "喜欢", "看电影"),
        _item("小杨", "喜欢", "看电影"),
    ]
    assert len(f.process(items)) == 1
