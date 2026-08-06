"""认知三元组实体单元测试"""

import asyncio
from datetime import datetime, timedelta

import pytest

from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension


def run(coro):
    return asyncio.run(coro)


class TestCognitiveTripleCreation:
    """三元组创建与基本属性"""

    def test_create_valid_triple(self):
        t = CognitiveTriple(
            subject="小杨",
            predicate="偏好语言",
            object="中文",
            dimension=Dimension.USER,
            user_id="user_1",
            confidence=0.95,
        )
        assert t.subject == "小杨"
        assert t.predicate == "偏好语言"
        assert t.object == "中文"
        assert t.dimension == Dimension.USER
        assert t.user_id == "user_1"
        assert t.confidence == 0.95
        assert t.is_active is True
        assert t.usage_count == 0

    def test_create_triple_with_all_dimensions(self):
        for dim in Dimension:
            t = CognitiveTriple(
                subject="test", predicate="has", object="value",
                dimension=dim, user_id="user_1",
            )
            assert t.dimension == dim

    def test_default_confidence(self):
        t = CognitiveTriple(
            subject="test", predicate="has", object="value",
            dimension=Dimension.WORLD, user_id="user_1",
        )
        assert t.confidence == 0.6

    def test_string_dimension_coerced(self):
        """字符串维度自动转换为枚举"""
        t = CognitiveTriple(
            subject="a", predicate="b", object="c",
            dimension="world", user_id="u1",
        )
        assert t.dimension == Dimension.WORLD

    def test_confidence_bounds(self):
        t1 = CognitiveTriple(
            subject="test", predicate="has", object="v",
            dimension=Dimension.WORLD, user_id="u1", confidence=0.0,
        )
        assert t1.confidence == 0.0
        t2 = CognitiveTriple(
            subject="test", predicate="has", object="v",
            dimension=Dimension.WORLD, user_id="u1", confidence=1.0,
        )
        assert t2.confidence == 1.0
        with pytest.raises(ValueError):
            CognitiveTriple(
                subject="test", predicate="has", object="v",
                dimension=Dimension.WORLD, user_id="u1", confidence=1.5,
            )


class TestTripleLifecycle:
    def test_expired(self):
        t = CognitiveTriple(
            subject="a", predicate="b", object="c",
            dimension=Dimension.STATE, user_id="u1",
            expires_at=datetime.now() - timedelta(days=1),
        )
        assert t.is_expired() is True

    def test_not_expired(self):
        t = CognitiveTriple(
            subject="a", predicate="b", object="c",
            dimension=Dimension.STATE, user_id="u1",
            expires_at=datetime.now() + timedelta(days=1),
        )
        assert t.is_expired() is False

    def test_never_expires(self):
        t = CognitiveTriple(
            subject="a", predicate="b", object="c",
            dimension=Dimension.WORLD, user_id="u1",
        )
        assert t.is_expired() is False

    def test_to_natural_language(self):
        t = CognitiveTriple(
            subject="小杨", predicate="偏好语言", object="中文",
            dimension=Dimension.USER, user_id="u1",
        )
        assert t.to_natural_language() == "小杨偏好语言中文。"


class TestDimensionEnum:
    def test_dimension_values(self):
        assert Dimension.USER.value == "user"
        assert Dimension.SELF.value == "self"
        assert Dimension.ENV.value == "env"
        assert Dimension.WORLD.value == "world"
        assert Dimension.STATE.value == "state"

    def test_dimension_from_string(self):
        assert Dimension("user") == Dimension.USER
        assert Dimension("self") == Dimension.SELF
        assert Dimension("env") == Dimension.ENV
        assert Dimension("world") == Dimension.WORLD
        assert Dimension("state") == Dimension.STATE