"""维度分流过滤器单元测试：triple/state/note/skip 路由规则"""

from aion_agent.pipeline.dimension_split_filter import DimensionSplitFilter


class TestTripleRouting:
    def test_user_triple(self):
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "小杨", "predicate": "偏好", "object": "中文",
             "dimension": "user", "confidence": 0.9},
        ], "u1")
        assert len(result.triples) == 1
        assert result.triples[0]["dimension"] == "user"
        assert result.triples[0]["confidence"] == 0.9

    def test_world_triple_default_dimension(self):
        """无 dimension 默认 world"""
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "ReAct", "predicate": "是", "object": "推理范式"},
        ], "u1")
        assert len(result.triples) == 1
        assert result.triples[0]["dimension"] == "world"

    def test_legacy_format_no_type(self):
        """旧格式无 type → 兼容为 triple"""
        result = DimensionSplitFilter().process([
            {"subject": "A", "predicate": "连接", "object": "B", "dimension": "world"},
        ], "u1")
        assert len(result.triples) == 1

    def test_missing_fields_skipped(self):
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "", "predicate": "是", "object": "B"},
        ], "u1")
        assert len(result.triples) == 0
        assert result.skipped == 1

    def test_expires_in_parsed(self):
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "任务", "predicate": "是", "object": "进行中",
             "dimension": "state", "expires_in": 7},
        ], "u1")
        assert result.triples[0]["expires_at"] is not None


class TestEnvSnapshotFilter:
    """env 快照过滤：关键词 + 数值模式"""

    def test_strong_keyword_git_status(self):
        """连续关键词 git状态 / git status → 直接判定快照"""
        for item in [
            {"type": "triple", "subject": "git状态", "predicate": "是", "object": "工作区干净",
             "dimension": "env"},
            {"type": "triple", "subject": "git", "predicate": "status", "object": "clean",
             "dimension": "env"},
        ]:
            result = DimensionSplitFilter().process([item], "u1")
            assert len(result.triples) == 0, f"应跳过: {item}"
            assert result.skipped == 1

    def test_number_pattern_skipped(self):
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "工作区", "predicate": "有", "object": "3 个未提交的变更",
             "dimension": "env"},
        ], "u1")
        assert len(result.triples) == 0
        assert result.skipped == 1

    def test_weak_keyword_without_number_kept(self):
        """弱关键词但没有数字 → 保留为配置级 env 认知"""
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "项目", "predicate": "使用", "object": "Python",
             "dimension": "env"},
        ], "u1")
        assert len(result.triples) == 1
        assert result.triples[0]["dimension"] == "env"


class TestStateRouting:
    def test_state_routed(self):
        result = DimensionSplitFilter().process([
            {"type": "state", "state_name": "学习中", "state_type": "task",
             "description": "阅读第8章", "priority": 3},
        ], "u1")
        assert len(result.states) == 1
        assert result.states[0]["state_name"] == "学习中"
        assert result.states[0]["priority"] == 3

    def test_state_missing_name_skipped(self):
        result = DimensionSplitFilter().process([
            {"type": "state", "description": "没有名字"},
        ], "u1")
        assert len(result.states) == 0
        assert result.skipped == 1


class TestNoteRouting:
    def test_note_routed(self):
        result = DimensionSplitFilter().process([
            {"type": "note", "title": "笔记", "content": "这是一段有实质内容的长文本笔记", "tags": ["a"]},
        ], "u1")
        assert len(result.notes) == 1
        assert result.notes[0]["title"] == "笔记"
        assert result.notes[0]["tags"] == ["a"]

    def test_placeholder_note_skipped(self):
        """占位笔记（过短/占位词）跳过"""
        for content in ["内容", "待补充", "无", "简短"]:
            result = DimensionSplitFilter().process([
                {"type": "note", "content": content},
            ], "u1")
            assert len(result.notes) == 0, f"应跳过: {content}"
            assert result.skipped == 1

    def test_missing_content_skipped(self):
        result = DimensionSplitFilter().process([
            {"type": "note", "title": "空笔记"},
        ], "u1")
        assert len(result.notes) == 0
        assert result.skipped == 1

    def test_title_auto_generated(self):
        content = "没有标题的长文本内容，足够长以作为笔记标题来源"
        result = DimensionSplitFilter().process([
            {"type": "note", "content": content},
        ], "u1")
        assert len(result.notes) == 1
        assert result.notes[0]["title"] == content[:20]

    def test_long_object_downgraded_to_note(self):
        """object > 200 字符 → 自动降级为 note"""
        long_obj = "长" * 250
        result = DimensionSplitFilter().process([
            {"type": "triple", "subject": "主题", "predicate": "包含", "object": long_obj,
             "dimension": "world"},
        ], "u1")
        assert len(result.triples) == 0
        assert len(result.notes) == 1
        assert long_obj in result.notes[0]["content"]

    def test_unknown_type_skipped(self):
        result = DimensionSplitFilter().process([
            {"type": "weird", "whatever": 1},
        ], "u1")
        assert result.skipped == 1


class TestDispatchResult:
    def test_non_dict_items_skipped(self):
        result = DimensionSplitFilter().process(["not a dict", 42], "u1")
        assert result.skipped == 2

    def test_empty_items(self):
        result = DimensionSplitFilter().process([], "u1")
        assert result.skipped == 0
        assert len(result.triples) == 0