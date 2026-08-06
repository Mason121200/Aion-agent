"""认知工具测试：搜索 / 创建 / 更新 / 删除 / 合并 / 确认"""

import pytest

from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.tools import ToolExecutor, ToolRegistry, register_cognition_tools


@pytest.fixture
def repo():
    return InMemoryCognitiveRepo(embedder=HashEmbedder())


@pytest.fixture
def executor(repo):
    registry = ToolRegistry()
    register_cognition_tools(registry, repo, user_id="u1")
    return ToolExecutor(registry)


async def _seed(repo):
    aid = await repo.save_triple(CognitiveTriple(
        subject="小杨", predicate="喜欢", object="看电影",
        dimension=Dimension.USER, user_id="u1", confidence=0.9,
    ))
    bid = await repo.save_triple(CognitiveTriple(
        subject="小杨", predicate="喜欢的语言", object="中文",
        dimension=Dimension.USER, user_id="u1", confidence=0.8,
    ))
    nid = await repo.save_note(Note(
        user_id="u1", note_type=NoteType.SUMMARY,
        title="电影讨论", content="我们聊了蜘蛛侠和特效",
    ))
    return aid, bid, nid


class TestSearchTools:
    def test_search_cognition(self, executor, repo):
        import asyncio
        asyncio.run(_seed(repo))
        result = asyncio.run(executor.execute(
            "search_cognition", {"query": "小杨", "top_k": 10}
        ))
        assert result.success
        data = result.data
        assert len(data["triples"]) == 2
        assert "小杨喜欢看电影" in data["content"]

    def test_search_by_relation(self, executor, repo):
        import asyncio
        asyncio.run(_seed(repo))
        result = asyncio.run(executor.execute(
            "search_by_relation", {"relation": "喜欢"}
        ))
        assert result.success
        assert len(result.data["triples"]) == 1

    def test_search_entity(self, executor, repo):
        import asyncio
        asyncio.run(_seed(repo))
        result = asyncio.run(executor.execute(
            "search_entity", {"entity": "小杨"}
        ))
        assert result.success
        assert len(result.data["triples"]) == 2

    def test_search_notes(self, executor, repo):
        import asyncio
        asyncio.run(_seed(repo))
        result = asyncio.run(executor.execute(
            "search_notes", {"query": "蜘蛛侠"}
        ))
        assert result.success
        assert len(result.data["notes"]) == 1


class TestModifyTools:
    def test_create_then_update_then_confirm(self, executor, repo):
        import asyncio

        created = asyncio.run(executor.execute(
            "create_cognition",
            {"subject": "小王", "predicate": "职业", "object": "程序员", "dimension": "user"},
        ))
        assert created.success
        rid = created.data["rel_id"]

        updated = asyncio.run(executor.execute(
            "update_cognition", {"rel_id": rid, "object": "产品经理"}
        ))
        assert updated.success
        triple = asyncio.run(executor.execute(
            "search_cognition", {"query": "小王"}
        ))
        assert "产品经理" in triple.data["content"]

        confirmed = asyncio.run(executor.execute(
            "confirm_cognition", {"rel_id": rid}
        ))
        assert confirmed.success
        assert confirmed.data["triple"]["is_confirmed_by_user"] is True

    def test_delete_and_merge(self, executor, repo):
        import asyncio
        aid, bid, _ = asyncio.run(_seed(repo))

        deleted = asyncio.run(executor.execute("delete_cognition", {"rel_id": aid}))
        assert deleted.success

        merged = asyncio.run(executor.execute(
            "merge_cognition", {"source_id": bid, "target_id": aid}
        ))
        assert merged.success
        assert merged.data["triple"]["rel_id"] == aid

    def test_missing_query_returns_error(self, executor):
        import asyncio
        result = asyncio.run(executor.execute("search_cognition", {}))
        assert not result.success
        assert "query" in result.error
