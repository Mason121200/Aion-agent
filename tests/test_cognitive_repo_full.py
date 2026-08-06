"""认知存储完整能力测试：merge / resolve / confirm / search / notes / 错题本

（同步测试壳 + asyncio.run，与项目现有测试风格一致，无需 pytest-asyncio）
"""

import asyncio

import pytest

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo


@pytest.fixture
def repo():
    return InMemoryCognitiveRepo(embedder=HashEmbedder())


@pytest.fixture
def sample_triple():
    return CognitiveTriple(
        subject="小杨", predicate="喜欢", object="看电影",
        dimension=Dimension.USER, user_id="u1", confidence=0.8,
    )


class TestMergeResolveConfirm:
    def test_save_triple_exact_dedup_merges(self, repo):
        a = CognitiveTriple(subject="A", predicate="是", object="1", user_id="u1", confidence=0.6)
        b = CognitiveTriple(subject="A", predicate="是", object="1", user_id="u1", confidence=0.9)
        aid = asyncio.run(repo.save_triple(a))
        bid = asyncio.run(repo.save_triple(b))
        assert aid == bid
        triple = asyncio.run(repo.get_triple(aid))
        assert triple.confidence == 0.9
        assert triple.usage_count == 1

    def test_merge_distinct_entities(self, repo):
        a = CognitiveTriple(subject="小杨", predicate="喜欢", object="看电影", user_id="u1", confidence=0.6)
        b = CognitiveTriple(subject="小杨", predicate="喜欢", object="看剧", user_id="u1", confidence=0.9)
        aid = asyncio.run(repo.save_triple(a))
        bid = asyncio.run(repo.save_triple(b))
        merged = asyncio.run(repo.merge_triples(bid, aid))
        assert merged.rel_id == aid
        assert merged.confidence == 0.9
        gone = asyncio.run(repo.get_triple(bid))
        assert gone.is_active is False

    def test_confirm_triple(self, repo, sample_triple):
        rid = asyncio.run(repo.save_triple(sample_triple))
        confirmed = asyncio.run(repo.confirm_triple(rid))
        assert confirmed.confidence == 1.0
        assert confirmed.is_confirmed_by_user is True

    def test_resolve_conflict_prefers_user_over_self(self, repo):
        self_triple = CognitiveTriple(
            subject="小杨", predicate="喜欢的语言", object="英文",
            dimension=Dimension.SELF, user_id="u1", confidence=0.7,
        )
        rid = asyncio.run(repo.save_triple(self_triple))
        resolved = asyncio.run(repo.resolve_conflict(rid, preferred_source="user"))
        assert resolved.confidence >= 0.95
        assert resolved.is_confirmed_by_user is True


class TestSearch:
    def test_search_triples_keyword_or(self, repo):
        asyncio.run(repo.save_triple(CognitiveTriple(subject="小杨", predicate="喜欢", object="看电影", user_id="u1")))
        asyncio.run(repo.save_triple(CognitiveTriple(subject="小王", predicate="喜欢", object="爬山", user_id="u1")))
        asyncio.run(repo.save_triple(CognitiveTriple(subject="小杨", predicate="是", object="程序员", user_id="u1")))
        hits = asyncio.run(repo.search_triples("u1", "小杨"))
        assert len(hits) == 2
        hits2 = asyncio.run(repo.search_triples("u1", "爬山 程序员"))  # OR
        assert len(hits2) == 2

    def test_search_triples_filters_inactive_and_expired(self, repo):
        a = CognitiveTriple(subject="X", predicate="是", object="1", user_id="u1")
        b = CognitiveTriple(subject="X", predicate="是", object="2", user_id="u1")
        aid = asyncio.run(repo.save_triple(a))
        bid = asyncio.run(repo.save_triple(b))
        asyncio.run(repo.delete_triple(aid))
        hits = asyncio.run(repo.search_triples("u1", "X"))
        assert [h.rel_id for h in hits] == [bid]

    def test_search_triples_dimension_filter(self, repo):
        asyncio.run(repo.save_triple(CognitiveTriple(subject="小杨", predicate="喜欢", object="电影", dimension=Dimension.USER, user_id="u1")))
        asyncio.run(repo.save_triple(CognitiveTriple(subject="ReAct", predicate="是", object="提示范式", dimension=Dimension.WORLD, user_id="u1")))
        hits = asyncio.run(repo.search_triples("u1", "小杨", dimension=Dimension.USER))
        assert len(hits) == 1
        assert hits[0].subject == "小杨"


class TestNotes:
    def test_update_note_content(self, repo):
        nid = asyncio.run(repo.save_note(Note(user_id="u1", title="t", content="old")))
        updated = asyncio.run(repo.update_note_content(nid, "new content"))
        assert updated.content == "new content"
        assert asyncio.run(repo.get_note(nid)).content == "new content"

    def test_create_or_update_note(self, repo):
        nid = asyncio.run(repo.create_or_update_note(
            user_id="u1", note_type="summary", content="第一次", title="会话摘要"
        ))
        nid2 = asyncio.run(repo.create_or_update_note(
            user_id="u1", note_type="summary", content="第二次", title="会话摘要",
            note_id=nid, overwrite=True,
        ))
        assert nid == nid2
        note = asyncio.run(repo.get_note(nid))
        assert note.content == "第二次"

    def test_search_notes_keyword_type(self, repo):
        asyncio.run(repo.save_note(Note(user_id="u1", note_type=NoteType.SUMMARY, title="电影笔记", content="我们聊了蜘蛛侠", tags=["电影"])))
        asyncio.run(repo.save_note(Note(user_id="u1", note_type=NoteType.KNOWLEDGE, title="AI", content="ReAct 是一种提示范式")))
        hits = asyncio.run(repo.search_notes("u1", query="蜘蛛侠"))
        assert len(hits) == 1
        hits2 = asyncio.run(repo.search_notes("u1", query="ReAct", note_type="knowledge"))
        assert len(hits2) == 1
        hits3 = asyncio.run(repo.search_notes("u1", query="不存在的内容"))
        assert hits3 == []


class TestCorrectionLog:
    def test_correction_log_records_operations(self, repo, sample_triple):
        rid = asyncio.run(repo.save_triple(sample_triple))
        asyncio.run(repo.confirm_triple(rid))
        asyncio.run(repo.update_triple(rid, object_="看剧"))
        asyncio.run(repo.delete_triple(rid))
        stats = asyncio.run(repo.get_correction_stats())
        ops = stats["by_operation"]
        assert ops.get("confirm") == 1
        assert ops.get("update") == 1
        assert ops.get("delete") == 1
        assert stats["total"] >= 3

    def test_correction_log_persisted(self, tmp_path):
        repo = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=str(tmp_path))
        triple = CognitiveTriple(subject="A", predicate="是", object="1", user_id="u1")
        rid = asyncio.run(repo.save_triple(triple))
        asyncio.run(repo.confirm_triple(rid))
        repo2 = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=str(tmp_path))
        stats = asyncio.run(repo2.get_correction_stats())
        assert stats["by_operation"].get("confirm") == 1
