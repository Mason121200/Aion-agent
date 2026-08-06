"""CognitionInjector —— 认知卡片注入器

从认知存储构建包含认知记忆的 System Prompt 片段。
MVP 简化：只保留「认知卡片 + 活跃状态 + 时间」三块，
裁掉任务块 / 长期计划 / 环境快照（它们属于任务编排层）。
"""

from __future__ import annotations

import logging
import re as _re
from datetime import datetime
from typing import Dict, List

from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo

logger = logging.getLogger(__name__)


class CognitionInjector:
    """将认知记忆注入 System Prompt（token 预算裁剪）"""

    def __init__(
        self,
        cognitive_repo: ICognitiveRepo,
        token_budget: int = 2000,
    ):
        self._cognitive_repo = cognitive_repo
        self._token_budget = token_budget

    # ===== Token 估算 =====

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算文本的 token 数（中文按 1.5、英文按 1.3、其余 0.25）"""
        if not text:
            return 0

        chinese_chars = len(_re.findall(
            r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text
        ))
        english_words = len(_re.findall(r"[a-zA-Z]+", text))
        remaining = len(text) - chinese_chars - sum(
            len(w) for w in _re.findall(r"[a-zA-Z]+", text)
        )

        tokens = int(
            chinese_chars * 1.5
            + english_words * 1.3
            + remaining * 0.25
        )
        return max(1, tokens)

    # ===== Token 预算裁剪 =====

    def _apply_cognition_budget(
        self,
        dim_labels: dict,
        grouped: dict,
    ) -> str:
        """按 Token 预算裁剪认知卡片

        分配比例：world 80% / state 10% / user+self+env 从 world 中匀出。
        超出预算时按顺序截断。

        【反回音室】state 预算 10%，防止短期情绪抢占语义权重。
        """
        total_budget = self._token_budget
        world_budget = int(total_budget * 0.8)
        state_budget = int(total_budget * 0.1)

        result_parts = ["\n\n## 🧠 已知认知记忆\n"]
        result_parts.append(
            "以下是此前对话中提取的认知信息，请在回答时参考这些上下文：\n"
            "⚠️「📊 状态追踪」为临时状态，仅供参考。\n"
        )

        # world 维度（80% 预算）
        world_items = grouped.get("world", [])
        if world_items:
            result_parts.append(f"### {dim_labels['world']}")
            world_text = ""
            for item in world_items:
                candidate = world_text + item + "\n"
                if self._estimate_tokens(candidate) > world_budget:
                    break
                world_text = candidate
            result_parts.append(world_text.rstrip())
            result_parts.append("")

        # state 维度（10% 预算）
        state_items = grouped.get("state", [])
        if state_items:
            result_parts.append(f"### {dim_labels['state']}")
            state_text = ""
            for item in state_items:
                candidate = state_text + item + "\n"
                if self._estimate_tokens(candidate) > state_budget:
                    break
                state_text = candidate
            result_parts.append(state_text.rstrip())
            result_parts.append("")

        # user/self/env 维度（从 world 剩余预算中分配）
        for dim_key in ["user", "self", "env"]:
            items = grouped.get(dim_key, [])
            if items:
                result_parts.append(f"### {dim_labels[dim_key]}")
                sub_budget = world_budget // 3
                sub_text = ""
                for item in items:
                    candidate = sub_text + item + "\n"
                    if self._estimate_tokens(candidate) > sub_budget:
                        break
                    sub_text = candidate
                result_parts.append(sub_text.rstrip())
                result_parts.append("")

        return "\n".join(result_parts)

    # ===== 认知规则模板（固定注入，不参与预算裁剪） =====

    @staticmethod
    def _build_rules_block() -> str:
        """认知提取规则模板：置信度赋值、时效性、五维分类、输出格式"""
        rules = """
### 🧠 认知提取规则（Agent 认知输出规范）

#### 1. 置信度（confidence）赋值规则

| 信息来源 | confidence | 说明 |
|----------|-----------|------|
| 用户直接陈述 | 0.90 - 1.00 | 明确表达的信息 |
| 用户隐含表达 | 0.80 - 0.90 | 间接透露但意图明确 |
| 工具执行结果 | 0.80 - 0.90 | 客观数据、文件内容 |
| LLM 归纳总结 | 0.60 - 0.75 | 综合分析得出的推论 |
| LLM 推断猜测 | 0.50 - 0.65 | 有依据但不确定的推测 |

#### 2. 时效性标记 expires_in（仅 state 维度）

| 认知类型 | 预估有效期 |
|----------|-----------|
| 用户情绪状态 | 1-3 天 |
| 当前任务/目标 | 7 天 |
| 阻塞问题 | 30 天或手动释放 |
| user/self/world/env 永久认知 | 不设过期（省略） |

#### 3. 五维认知分类标准

- 👤 user（用户画像）：姓名、偏好、技能、习惯、价值观、技术栈偏好
- 🤖 self（助手自身）：能力边界、配置变更、版本信息、用户反馈
- 💻 env（环境信息）：项目根路径、操作系统、数据库选型等长期稳定配置
- 🌍 world（客观知识）：开发规范、架构设计意图、技术决策及其理由
- 📊 state（状态追踪）— 有生命周期：当前开发阶段、阻塞问题、待办任务

#### 4. 输出格式

在回复末尾输出一个认知块：

<!--COGNITION_START-->
[{"type": "triple", "subject": "...", "predicate": "...", "object": "...", "dimension": "world", "confidence": 0.8}, {"type": "state", "state_name": "...", "state_type": "task", "description": "...", "expires_in": 7}]
<!--COGNITION_END-->
"""
        return rules

    _INJECT_CONTEXT_NOTE = """
### 🔄 上下文注入机制（自动）

每次请求开始时，系统会自动注入一条【动态上下文】system 消息，
其中包含：当前时间、用户标识、认知记忆卡片与活跃状态。
该消息由系统程序化注入，无需你调用任何工具，也无需回复或总结它。
"""

    # ===== 静态 System Prompt（缓存友好） =====

    def build_static_system_prompt(self, base_prompt: str = "") -> str:
        """构建静态 System Prompt（认知规则 + 注入机制说明）

        静态部分不参与预算裁剪，可整块命中前缀缓存。
        """
        return base_prompt + self._build_rules_block() + self._INJECT_CONTEXT_NOTE

    # ===== 动态上下文组装 =====

    async def _build_dynamic_text(
        self,
        user_id: str,
        current_message: str = "",
    ) -> str:
        """组装动态上下文文本：认知卡片 + 活跃状态 + 时间"""
        now = datetime.now()
        time_header = (
            f"\n## 📅 当前会话\n"
            f"- 当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}"
            f"（{now.strftime('%A')}）\n"
            f"- 用户标识：{user_id}\n"
        )

        # === 大脑皮层：认知三元组 ===
        try:
            query = current_message if current_message else "*"
            all_triples = await self._cognitive_repo.retrieve(
                user_id=user_id,
                query=query,
                top_k=20,
                min_confidence=0.6,
            )
            if all_triples is None:
                all_triples = []
            if not all_triples and query != "*":
                # 检索无命中时回退全量记忆，保证记忆对 LLM 始终可见（体验关键）
                all_triples = await self._cognitive_repo.retrieve(
                    user_id=user_id,
                    query="*",
                    top_k=20,
                    min_confidence=0.6,
                ) or []
        except Exception as e:
            logger.warning(f"认知加载失败，使用纯净 prompt: {e}")
            all_triples = []

        grouped: Dict[str, List[str]] = {
            "user": [], "self": [], "env": [],
            "world": [], "state": [],
        }
        dim_labels = {
            "user": "👤 用户画像",
            "self": "🤖 助手自身",
            "env": "💻 环境信息",
            "world": "🌍 客观知识",
            "state": "📊 状态追踪",
        }

        for t in all_triples:
            dim = t.dimension.value
            if dim not in grouped:
                continue

            confidence_tag = f"（置信度：{t.confidence:.0%}）"
            expires_tag = ""
            if t.expires_at:
                if t.expires_at > datetime.now():
                    days_left = (t.expires_at - datetime.now()).days
                    if days_left <= 7:
                        expires_tag = f" ⚠️{days_left}天后过期"

            line = (
                f"- {t.to_natural_language()}"
                f"{confidence_tag}{expires_tag}"
            )
            grouped[dim].append(line)

        # === 活跃状态 ===
        active_states = []
        try:
            active_states = await self._cognitive_repo.get_active_states(user_id)
            if active_states is None:
                active_states = []
        except Exception as e:
            logger.warning(f"状态加载失败: {e}")

        # === 组装注入块 ===
        cognition_text = self._apply_cognition_budget(dim_labels, grouped)

        state_block = ""
        if active_states:
            # 【反回音室】标题降权 + 添加反锚定提示
            state_lines = [
                "\n## ⚠️ 当前活跃状态\n",
                "以下为临时任务状态，仅作进度参考。"
                "【重要】不要被用户情绪状态（如「迷茫」）锚定——"
                "优先以用户当前消息的语义决定回应方向，而非追问情绪。\n",
            ]
            state_text = ""
            state_budget = int(self._token_budget * 0.1)
            for s in active_states:
                icon = {
                    "task": "📋", "agent": "🤖", "user": "😊",
                }.get(s.state_type, "📌")
                desc = s.description or ""
                if s.expires_at:
                    try:
                        days = (s.expires_at - datetime.now()).days
                        desc += f"（{days}天后释放）"
                    except Exception:
                        pass
                line = (
                    f"- {icon} [{s.state_type}] "
                    f"{s.state_name}: {desc}"
                )
                candidate = state_text + line + "\n"
                if self._estimate_tokens(candidate) > state_budget:
                    break
                state_text = candidate
            if state_text:
                state_block = "\n".join(state_lines) + state_text

        return cognition_text + state_block + time_header

    async def build_dynamic_context(
        self,
        user_id: str,
        current_message: str = "",
    ) -> str:
        """构建动态上下文文本（System Prompt 的动态片段）"""
        return await self._build_dynamic_text(user_id, current_message)

    async def build(
        self,
        user_id: str,
        current_message: str = "",
        base_prompt: str = "",
    ) -> str:
        """构建完整 System Prompt（静态规则 + 动态上下文）"""
        return (
            self.build_static_system_prompt(base_prompt=base_prompt)
            + await self._build_dynamic_text(user_id, current_message)
        )