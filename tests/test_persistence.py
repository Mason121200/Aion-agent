"""记忆持久化测试：重启后三元组/状态/笔记/向量索引完整恢复"""

import asyncio
import shutil
from pathlib import Path

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo

TEST_DIR = Path(__file__).resolve().parent.parent / "aion_agent" / "data" / "test_persist"


def run(coro):
    return asyncio.run(coro)


def _fresh_dir():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_DIR


class TestRepoPersistence:
    def test_roundtrip_restores_all_memory(self):
        d = _fresh_dir()
        try:
            repo1 = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=d)
            run(repo1.save_triple(CognitiveTriple(
                subject="小杨", predicate="偏好语言", object="中文",
                dimension=Dimension.USER, user_id="u1", confidence=0.95,
            )))
            run(repo1.save_state(AgentState(
                user_id="u1", state_type="task", state_name="学习中",
                description="阅读第8章",
            )))
            run(repo1.save_note(Note(
                user_id="u1", title="第8章笔记", content="这是持久化的笔记内容",
                note_type=NoteType.LONG_TEXT,
            )))

            # 模拟重启：用同一个目录新建仓库
            repo2 = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=d)

            triples = run(repo2.retrieve("u1", query="*"))
            assert len(triples) == 1
            assert triples[0].subject == "小杨"
            assert triples[0].confidence == 0.95

            states = run(repo2.get_active_states("u1"))
            assert len(states) == 1
            assert states[0].state_name == "学习中"

            notes = run(repo2.get_notes_for_injection("u1"))
            assert len(notes) == 1
            assert notes[0].title == "第8章笔记"

            # 向量索引也在重启后可用（新会话查询能命中旧记忆）
            hits = run(repo2.retrieve("u1", query="小杨的语言偏好", top_k=3))
            assert hits[0].subject == "小杨"
        finally:
            shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_dedup_index_rebuilt_after_load(self):
        d = _fresh_dir()
        try:
            repo1 = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=d)
            run(repo1.save_triple(CognitiveTriple(
                subject="A", predicate="是", object="B",
                dimension=Dimension.WORLD, user_id="u1",
            )))
            repo2 = InMemoryCognitiveRepo(embedder=HashEmbedder(), persist_dir=d)
            # 重启后再存同一条 → 应合并而不是新增
            run(repo2.save_triple(CognitiveTriple(
                subject="A", predicate="是", object="B",
                dimension=Dimension.WORLD, user_id="u1", confidence=0.9,
            )))
            triples = run(repo2.retrieve("u1", query="*"))
            assert len(triples) == 1
            assert triples[0].confidence == 0.9
            assert triples[0].usage_count == 1
        finally:
            shutil.rmtree(TEST_DIR, ignore_errors=True)

    def test_no_persist_dir_keeps_memory_only(self):
        """未指定 persist_dir → 不写盘、不读盘（纯内存）"""
        repo = InMemoryCognitiveRepo(embedder=HashEmbedder())
        run(repo.save_triple(CognitiveTriple(
            subject="临时", predicate="是", object="记忆",
            dimension=Dimension.WORLD, user_id="u1",
        )))
        assert repo._persist_file is None
        triples = run(repo.retrieve("u1", query="*"))
        assert len(triples) == 1
