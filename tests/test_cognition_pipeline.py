"""认知管道端到端测试：流式处理 → 分流 → 存储 → 检索"""

import asyncio

from aion_agent.core.entities.cognitive_triple import Dimension
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.use_cases.cognition_handler import process_cognition_block


def run(coro):
    return asyncio.run(coro)


def _block(items) -> str:
    """构造含认知块的流文本"""
    import json
    return (
        "可见回复内容\n"
        "<!--COGNITION_START-->"
        f"{json.dumps(items, ensure_ascii=False)}"
        "<!--COGNITION_END-->"
    )


class TestProcessStream:
    def test_full_loop(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        text = _block([
            {"type": "triple", "subject": "小杨", "predicate": "偏好语言",
             "object": "中文", "dimension": "user", "confidence": 0.9},
            {"type": "state", "state_name": "学习中", "state_type": "task",
             "description": "第8章"},
        ])
        chunks = [text[i:i + 17] for i in range(0, len(text), 17)]

        visible, result = run(pipeline.process_stream(chunks, "u1"))

        assert "可见回复内容" in "".join(visible)
        assert len(result.triples) == 1
        assert len(result.states) == 1
        assert result.store_success is True

        triples = run(repo.retrieve("u1", query="*"))
        assert len(triples) == 1
        assert triples[0].subject == "小杨"

        states = run(repo.get_active_states("u1"))
        assert len(states) == 1
        assert states[0].state_name == "学习中"

    def test_no_cognition_blocks(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        visible, result = run(
            pipeline.process_stream(["只有可见文本，没有认知块"], "u1")
        )
        assert "".join(visible) == "只有可见文本，没有认知块"
        assert len(result.triples) == 0


class TestDedupMerge:
    def test_same_triple_merged(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        block = _block([
            {"type": "triple", "subject": "小杨", "predicate": "偏好语言",
             "object": "中文", "dimension": "user", "confidence": 0.9},
        ])

        run(pipeline.process_stream([block], "u1"))
        run(pipeline.process_stream([block], "u1"))

        triples = run(repo.retrieve("u1", query="*"))
        assert len(triples) == 1  # 去重后仍是一条
        assert triples[0].confidence == 0.9
        assert triples[0].usage_count == 1  # 第二次保存触发合并计数

    def test_higher_confidence_wins(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)

        run(process_cognition_block("u1", _block([
            {"type": "triple", "subject": "A", "predicate": "是", "object": "B",
             "dimension": "world", "confidence": 0.7},
        ]), repo, pipeline))
        run(process_cognition_block("u1", _block([
            {"type": "triple", "subject": "A", "predicate": "是", "object": "B",
             "dimension": "world", "confidence": 0.95},
        ]), repo, pipeline))

        triples = run(repo.retrieve("u1", query="*"))
        assert triples[0].confidence == 0.95


class TestRetrieval:
    def test_keyword_retrieve_filters_dimension(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        run(pipeline.process_batch([
            {"type": "triple", "subject": "小杨", "predicate": "偏好语言",
             "object": "中文", "dimension": "user", "confidence": 0.9},
            {"type": "triple", "subject": "Python", "predicate": "是",
             "object": "编程语言", "dimension": "world", "confidence": 0.9},
        ], "u1"))

        world_only = run(repo.retrieve(
            "u1", query="*", dimensions=[Dimension.WORLD],
        ))
        assert len(world_only) == 1
        assert world_only[0].subject == "Python"

    def test_vector_retrieve_ranks_by_relevance(self):
        repo = InMemoryCognitiveRepo(embedder=HashEmbedder())
        pipeline = CognitionPipeline(cognitive_repo=repo)
        run(pipeline.process_batch([
            {"type": "triple", "subject": "小杨", "predicate": "偏好语言",
             "object": "中文", "dimension": "user", "confidence": 0.9},
            {"type": "triple", "subject": "Python", "predicate": "是",
             "object": "编程语言", "dimension": "world", "confidence": 0.9},
        ], "u1"))

        results = run(repo.retrieve("u1", query="小杨的偏好", top_k=3))
        assert results[0].subject == "小杨"

    def test_soft_delete_excluded(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        run(pipeline.process_batch([
            {"type": "triple", "subject": "旧", "predicate": "是", "object": "旧知识",
             "dimension": "world", "confidence": 0.9},
        ], "u1"))
        triples = run(repo.retrieve("u1", query="*"))
        run(repo.delete_triple(triples[0].rel_id, soft=True))
        assert run(repo.retrieve("u1", query="*")) == []


class TestHandler:
    def test_process_block_with_markers(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        summary = run(process_cognition_block(
            "u1", _block([
                {"type": "triple", "subject": "ReAct", "predicate": "是",
                 "object": "推理范式", "dimension": "world"},
            ]),
            repo, pipeline,
        ))
        assert summary["triples"] == 1
        assert summary["store_success"] is True

    def test_process_block_invalid_json(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        summary = run(process_cognition_block(
            "u1", "<!--COGNITION_START-->完全不是 JSON<!--COGNITION_END-->",
            repo, pipeline,
        ))
        assert summary["triples"] == 0
        assert summary["store_success"] is True

    def test_process_block_scans_mixed_objects(self):
        """JSON 整体解析失败时，兜底提取 {..} 对象块"""
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        messy = (
            "前缀文本 "
            '{"type":"triple","subject":"A","predicate":"连接","object":"B","dimension":"world"}'
            " 中间文本 "
            '{"type":"triple","subject":"C","predicate":"连接","object":"D","dimension":"world"}'
        )
        summary = run(process_cognition_block("u1", messy, repo, pipeline))
        assert summary["triples"] == 2