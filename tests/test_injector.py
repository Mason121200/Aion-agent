"""CognitionInjector 单元测试：token 预算裁剪 + 维度分组 + 状态注入"""

import asyncio

from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.use_cases.cognition_injector import CognitionInjector


def run(coro):
    return asyncio.run(coro)


def _seed_repo():
    repo = InMemoryCognitiveRepo()
    pipeline = CognitionPipeline(cognitive_repo=repo)
    run(pipeline.process_batch([
        {"type": "triple", "subject": "小杨", "predicate": "偏好语言",
         "object": "中文", "dimension": "user", "confidence": 0.9},
        {"type": "triple", "subject": "ReAct", "predicate": "是",
         "object": "一种推理范式", "dimension": "world", "confidence": 0.9},
        {"type": "triple", "subject": "Reflexion", "predicate": "是",
         "object": "带反思的推理范式", "dimension": "world", "confidence": 0.85},
        {"type": "triple", "subject": "学习进度", "predicate": "处于",
         "object": "第8章", "dimension": "state", "confidence": 0.8,
         "expires_in": 7},
        {"type": "state", "state_name": "学习中", "state_type": "task",
         "description": "阅读复杂推理", "priority": 3},
    ], "u1"))
    return repo


class TestTokenEstimation:
    def test_chinese_weighted_higher(self):
        zh = CognitionInjector._estimate_tokens("中文内容" * 10)
        en = CognitionInjector._estimate_tokens("hello world" * 10)
        assert zh > en
        assert zh > 0

    def test_empty(self):
        assert CognitionInjector._estimate_tokens("") == 0
        assert CognitionInjector._estimate_tokens(None) == 0


class TestBudgetTrimming:
    def test_small_budget_trims(self):
        injector = CognitionInjector(cognitive_repo=None, token_budget=50)
        grouped = {
            "user": [], "self": [], "env": [],
            "world": ["- 长内容" * 30],
            "state": [],
        }
        dim_labels = {"world": "🌍 客观知识", "state": "📊 状态追踪",
                      "user": "👤 用户画像", "self": "🤖 助手自身",
                      "env": "💻 环境信息"}
        text = injector._apply_cognition_budget(dim_labels, grouped)
        # 预算 50，world 预算 40，内容远超 → 应被截断
        assert injector._estimate_tokens(text) <= 200

    def test_large_budget_keeps_all(self):
        injector = CognitionInjector(cognitive_repo=None, token_budget=5000)
        grouped = {
            "user": ["- 用户条目"], "self": [], "env": [],
            "world": ["- 世界条目"], "state": ["- 状态条目"],
        }
        dim_labels = {"world": "w", "state": "s", "user": "u", "self": "x", "env": "e"}
        text = injector._apply_cognition_budget(dim_labels, grouped)
        assert "世界条目" in text
        assert "状态条目" in text
        assert "用户条目" in text


class TestDynamicContext:
    def test_groups_by_dimension(self):
        repo = _seed_repo()
        injector = CognitionInjector(repo, token_budget=2000)
        # query 为空 → 全量检索（关键词模式）
        text = run(injector.build_dynamic_context("u1", current_message=""))
        assert "🌍 客观知识" in text
        assert "👤 用户画像" in text
        assert "📊 状态追踪" in text
        assert "ReAct是一种推理范式" in text
        assert "小杨偏好语言中文" in text
        assert "当前活跃状态" in text
        assert "学习中" in text
        assert "📅 当前会话" in text

    def test_keyword_query_filters_results(self):
        """关键词检索：query 必须是三元组文本的子串"""
        repo = _seed_repo()
        injector = CognitionInjector(repo, token_budget=2000)
        text = run(injector.build_dynamic_context("u1", current_message="学习"))
        # 只有「学习进度处于第8章」包含「学习」子串
        assert "ReAct是一种推理范式" not in text
        assert "学习进度处于第8章" in text

    def test_rules_block_in_static_prompt(self):
        injector = CognitionInjector(cognitive_repo=None)
        prompt = injector.build_static_system_prompt(base_prompt="基础提示\n")
        assert prompt.startswith("基础提示")
        assert "认知提取规则" in prompt
        assert "COGNITION_START" in prompt
        assert "上下文注入机制" in prompt

    def test_build_combines_static_and_dynamic(self):
        repo = _seed_repo()
        injector = CognitionInjector(repo)
        prompt = run(injector.build("u1", current_message="", base_prompt="基础"))
        assert "基础" in prompt
        assert "认知提取规则" in prompt
        assert "🧠 已知认知记忆" in prompt

    def test_expiry_warning(self):
        repo = InMemoryCognitiveRepo()
        pipeline = CognitionPipeline(cognitive_repo=repo)
        run(pipeline.process_batch([
            {"type": "triple", "subject": "临时", "predicate": "是", "object": "短期认知",
             "dimension": "state", "confidence": 0.8, "expires_in": 2},
        ], "u1"))
        injector = CognitionInjector(repo)
        text = run(injector.build_dynamic_context("u1"))
        assert "天后过期" in text