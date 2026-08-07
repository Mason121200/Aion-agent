"""默认技能目录 —— 底座内置技能的组装

把各工具集的注册函数闭包成 Skill 实例，供 AppRuntime / ReActChatSession 一键安装。
以后新增能力 = 在目录里加一个 Skill，底座代码不变。
"""

from __future__ import annotations

from typing import List, Optional

from aion_agent.skills.base import Skill


def build_default_skills(
    *,
    cognitive_repo=None,
    study_repo=None,
    planner_repo=None,
    user_id: str = "chat_user",
) -> List[Skill]:
    """组装底座内置技能。planner_repo / study_repo 未提供时对应技能不安装。"""
    from aion_agent.tools.builtin import register_builtin_tools
    from aion_agent.tools.cognition_tools import register_cognition_tools
    from aion_agent.tools.planner_tools import register_planner_tools
    from aion_agent.tools.study_tools import register_study_tools

    skills: List[Skill] = [
        Skill(
            name="builtin",
            version="1.0.0",
            level="builtin",
            description="通用基础工具：时间 / 计算器 / 文件读写与列表 / 网页抓取 / 白名单命令（随框架固化）",
            tools=[
                "get_current_time", "calculator", "read_file", "file_write",
                "file_list", "web_fetch", "shell",
            ],
            register_func=register_builtin_tools,
        ),
        Skill(
            name="cognition",
            version="1.0.0",
            level="system",
            description="认知层工具：主动回忆 / 写入 / 修正（框架契约，不可禁用）",
            tools=[
                "search_cognition", "search_by_relation", "search_entity",
                "search_notes", "create_cognition", "update_cognition",
                "delete_cognition", "merge_cognition", "confirm_cognition",
            ],
            register_func=lambda reg: register_cognition_tools(
                reg, cognitive_repo, user_id=user_id
            ),
        ),
    ]
    if planner_repo is not None:
        skills.append(
            Skill(
                name="planner",
                version="1.0.0",
                description="通用规划器：长期任务规划 / 进度追溯 / 动态调整",
                tools=[
                    "task_create", "task_list", "task_read", "task_update",
                    "task_checkin", "task_archive", "task_milestone",
                    "task_complete_milestone",
                ],
                register_func=lambda reg: register_planner_tools(
                    reg, planner_repo, cognitive_repo=cognitive_repo,
                    user_id=user_id,
                ),
            )
        )
    if study_repo is not None:
        skills.append(
            Skill(
                name="study",
                version="1.0.0",
                description="学习场景：学习计划 / 资料 / 学习记录 / 提醒",
                tools=[
                    "plan_create", "plan_list", "plan_read", "plan_update",
                    "plan_checkin", "plan_archive", "add_study_material",
                    "search_study_materials", "log_study_session",
                    "create_reminder", "list_reminders", "complete_reminder",
                    "get_study_status",
                ],
                register_func=lambda reg: register_study_tools(
                    reg, study_repo, cognitive_repo=cognitive_repo,
                    user_id=user_id,
                ),
            )
        )
    return skills
