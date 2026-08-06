"""Aion Agent 命令行入口

用法：
    python -m aion_agent chat [--no-tools]   # ✨ 与 LLM 对话：ReAct 循环 + 记忆沉淀（体验模式）
    python -m aion_agent demo                # 离线全链路演示（无 LLM 也可跑）
    python -m aion_agent extract <file>      # 从文本文件提取认知块并入库
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.use_cases.cognition_handler import process_cognition_block
from aion_agent.use_cases.cognition_injector import CognitionInjector

USER_ID = "demo_user"

# 演示数据目录：aion_agent/data/demo_vector
_DEMO_DIR = Path(__file__).resolve().parent / "data" / "demo_vector"


def _ensure_utf8_stdio() -> None:
    """Windows 管道输出时避免 GBK 编码崩溃（emoji 等字符）"""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


# ==================== demo：离线全链路演示 ====================

def _turn1_text() -> str:
    """第 1 轮：模拟 LLM 流式输出（可见文本 + 认知块）"""
    return (
        "好的，我来帮你梳理复杂推理的学习路径。\n"
        "<!--COGNITION_START-->"
        "["
        '{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.95},'
        '{"type":"triple","subject":"学习目标","predicate":"是","object":"掌握大语言模型的复杂推理方法","dimension":"state","confidence":0.8,"expires_in":30},'
        '{"type":"triple","subject":"ReAct","predicate":"是","object":"一种让模型交替推理与行动的提示范式","dimension":"world","confidence":0.9},'
        '{"type":"triple","subject":"工作区","predicate":"有","object":"3 个未提交的变更","dimension":"env","confidence":0.7},'
        '{"type":"state","state_name":"学习第8章","state_type":"task","description":"正在阅读复杂推理章节","priority":3,"expires_in":7},'
        '{"type":"note","title":"第8章笔记","content":"ReAct 强调推理轨迹与行动交错，Reflexion 增加反思环节用于纠错。这是复杂推理的核心方法。","tags":["llm","chapter8"]}'
        "]"
        "<!--COGNITION_END-->"
    )


def _turn2_block() -> str:
    """第 2 轮：重复 user 三元组（演示精确去重合并）+ 新增 world 三元组"""
    return (
        "<!--COGNITION_START-->"
        "["
        '{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.98},'
        '{"type":"triple","subject":"Reflexion","predicate":"是","object":"一种带反思纠错的推理范式","dimension":"world","confidence":0.85}'
        "]"
        "<!--COGNITION_END-->"
    )


def _chunk(text: str, size: int = 40) -> list:
    """把整段文本切成小块，演示流式状态机对任意切块边界的处理"""
    return [text[i:i + size] for i in range(0, len(text), size)]


async def _run_demo(repo, pipeline, injector) -> None:
    print("=" * 60)
    print("Aion Agent —— 认知闭环演示（零 LLM 依赖，离线验证管道）")
    print("=" * 60)
    print("提示：想体验「对话中自动沉淀记忆」，请配置 LLM 后运行")
    print("      python -m aion_agent chat")

    print("\n【第 1 轮】模拟 LLM 流式输出（含认知块，切成小块）…")
    chunks = _chunk(_turn1_text())
    visible_chunks, result = await pipeline.process_stream(chunks, USER_ID)

    print("\n--- 可见文本（流式透出）---")
    for v in visible_chunks:
        print(v, end="")
    print("\n\n--- 分流结果 ---")
    print(
        f"triples={len(result.triples)}  states={len(result.states)}  "
        f"notes={len(result.notes)}  skipped={result.skipped}  "
        f"store_success={result.store_success}"
    )
    print("skipped 明细：env 快照「工作区有 3 个未提交的变更」被快照过滤拦截")

    print("\n【第 2 轮】再次出现 user 三元组（置信度 0.98）→ 精确去重合并…")
    summary = await process_cognition_block(USER_ID, _turn2_block(), repo, pipeline)
    print(
        f"triples={summary['triples']}  states={summary['states']}  "
        f"notes={summary['notes']}  skipped={summary['skipped']}"
    )

    print("\n--- 记忆统计 ---")
    from aion_agent.core.entities.cognitive_triple import Dimension

    dim_labels = {
        Dimension.USER: "user(用户)", Dimension.SELF: "self(助手)",
        Dimension.ENV: "env(环境)", Dimension.WORLD: "world(知识)",
        Dimension.STATE: "state(状态)",
    }
    for dim in Dimension:
        triples = await repo.list_triples_by_dimension(USER_ID, dim)
        if triples:
            print(f"  {dim_labels[dim]}: {len(triples)} 条")
            for t in triples:
                print(
                    f"    - {t.to_natural_language()} "
                    f"(conf={t.confidence:.0%}, usage={t.usage_count})"
                )
    states = await repo.get_active_states(USER_ID)
    if states:
        print(f"  活跃状态: {len(states)} 个")
        for s in states:
            print(f"    - [{s.state_type}] {s.state_name}: {s.description}")
    notes = await repo.get_notes_for_injection(USER_ID)
    if notes:
        print(f"  笔记: {len(notes)} 篇")
        for n in notes:
            print(f"    - 《{n.title}》")

    print("\n--- RAG 注入（current_message=「我最近在学什么？」）---")
    context = await injector.build_dynamic_context(
        USER_ID, current_message="我最近在学什么？"
    )
    print(context)


def run_demo(reset: bool = True) -> int:
    """运行认知闭环演示"""
    _ensure_utf8_stdio()
    if reset:
        shutil.rmtree(_DEMO_DIR, ignore_errors=True)

    repo = InMemoryCognitiveRepo(
        embedder=HashEmbedder(),
        persist_dir=_DEMO_DIR,
    )
    pipeline = CognitionPipeline(cognitive_repo=repo)
    injector = CognitionInjector(repo, token_budget=2000)

    asyncio.run(_run_demo(repo, pipeline, injector))
    return 0


# ==================== extract：处理自己的文本 ====================

async def _extract_file(path: str, user_id: str) -> None:
    """从文本文件提取认知块并入库"""
    _ensure_utf8_stdio()
    text = Path(path).read_text(encoding="utf-8")
    chunks = [text[i:i + 64] for i in range(0, len(text), 64)]

    repo = InMemoryCognitiveRepo(embedder=HashEmbedder())
    pipeline = CognitionPipeline(cognitive_repo=repo)

    visible_chunks, result = await pipeline.process_stream(chunks, user_id)

    print("--- 可见文本 ---")
    for v in visible_chunks:
        print(v, end="")
    print("\n\n--- 分流结果 ---")
    print(
        f"triples={len(result.triples)}  states={len(result.states)}  "
        f"notes={len(result.notes)}  skipped={result.skipped}  "
        f"store_success={result.store_success}"
    )


# ==================== chat：ReAct 对话体验 ====================

async def _chat_loop(session) -> None:
    print("输入消息开始对话（输入 quit / exit / 退出 结束）：")
    while True:
        try:
            msg = input("你 > ")
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        msg = msg.strip()
        if not msg:
            continue
        if msg.lower() in ("quit", "exit", "q") or msg == "退出":
            print("再见！")
            break

        print("助手 > ", end="", flush=True)
        totals = {"triples": 0, "states": 0, "notes": 0, "skipped": 0, "total": 0}
        try:
            async for event in session.react_stream(msg):
                event_type = event.get("type")
                if event_type == "reasoning":
                    print(f"\n  🤔 {event.get('content', '')}", end="", flush=True)
                elif event_type == "token":
                    print(event.get("content", ""), end="", flush=True)
                elif event_type == "tool_call":
                    name = event.get("name", "")
                    args = event.get("args") or {}
                    arg_text = ", ".join(
                        f"{k}={v}" for k, v in args.items()
                    ) if isinstance(args, dict) else str(args)
                    print(f"\n  🛠️ 调用工具: {name}({arg_text})", flush=True)
                elif event_type == "tool_result":
                    tc = event.get("tool_call") or {}
                    ok = tc.get("success", False)
                    content = (
                        str(tc.get("data") or tc.get("error") or "")[:160]
                    )
                    print(f"  {'✅' if ok else '❌'} 观察结果: {content}", flush=True)
                elif event_type == "reflect":
                    print(
                        f"  🔄 反思: {event.get('reason', '')}",
                        flush=True,
                    )
                elif event_type == "context":
                    print(f"  📐 {event.get('note', '')}", flush=True)
                elif event_type == "budget_exhausted":
                    print(f"  ⚠️ {event.get('note', '')}", flush=True)
                elif event_type == "cognition":
                    for key in totals:
                        totals[key] += event.get(key, 0)
                elif event_type == "error":
                    print(f"\n[出错] {event.get('error', '')}", flush=True)
                elif event_type == "final":
                    final = event.get("content", "")
                    # 正文已流式输出；仅补打「步数耗尽」的收尾说明
                    if final.startswith("（已达到最大步数") and "---\n" in final:
                        note = final.split("---\n", 1)[-1]
                        print(f"\n{note}", end="", flush=True)
        except Exception as e:
            print(f"\n[对话出错] {e}")
            continue
        print()
        if totals["total"] > 0:
            print(
                f"  🧠 记忆沉淀：+{totals['triples']} 三元组 / "
                f"+{totals['states']} 状态 / +{totals['notes']} 笔记"
                f"（跳过 {totals['skipped']}）"
            )
            print("  （下一轮提问时，这些记忆会自动注入，试试「我叫什么？」）")


def cmd_chat(args) -> int:
    """启动 ReAct 对话体验模式"""
    _ensure_utf8_stdio()
    from aion_agent.llm.openai_compatible import (
        OpenAICompatibleClient,
        get_config,
        load_env_from_dotenv,
    )
    from aion_agent.use_cases.react_chat_session import ReActChatSession

    # 加载项目根目录 .env（若存在）
    load_env_from_dotenv(Path.cwd() / ".env")

    cfg = get_config()
    if not cfg["api_key"]:
        print("未检测到 API Key。请设置环境变量或在项目根目录创建 .env：")
        print("  AION_LLM_API_KEY=sk-xxx")
        print("（Base URL 与模型已内置 DeepSeek 默认值：")
        print("   https://api.deepseek.com/v1  /  deepseek-v4-flash）")
        return 1

    repo = InMemoryCognitiveRepo(
        embedder=HashEmbedder(),
        persist_dir=Path(__file__).resolve().parent / "data" / "chat",
    )
    llm = OpenAICompatibleClient(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        model=cfg["model"],
    )
    session = ReActChatSession(
        llm,
        cognitive_repo=repo,
        user_id=args.user,
        tools_enabled=not args.no_tools,
        max_steps=args.max_steps,
        max_tokens_budget=args.token_budget,
    )

    print("=" * 60)
    print("Aion Agent 对话体验模式（ReAct 循环 + 记忆自动沉淀）")
    print(f"模型: {llm.model}  |  接口: {llm.base_url}")
    if not args.no_tools:
        print(
            "工具: get_current_time / calculator / read_file"
            "（无需工具时模型直接回答并结束循环）"
        )
    print(f"步数上限: {session._max_steps}  |  Token 预算: {session._max_tokens_budget}")
    print("=" * 60)
    asyncio.run(_chat_loop(session))
    return 0


# ==================== 入口 ====================

def main(argv=None) -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="aion",
        description="Aion Agent —— 认知 MVP（ReAct 循环 + 记忆沉淀 + RAG 注入）",
    )
    sub = parser.add_subparsers(dest="command")

    chat_p = sub.add_parser(
        "chat", help="✨ 与 LLM 对话：ReAct 循环 + 记忆自动沉淀（体验模式）"
    )
    chat_p.add_argument("--user", default="chat_user", help="用户标识")
    chat_p.add_argument(
        "--no-tools", action="store_true", help="禁用工具调用（纯对话）"
    )
    chat_p.add_argument("--max-steps", type=int, default=8, help="ReAct 最大步数")
    chat_p.add_argument(
        "--token-budget", type=int, default=8000, help="全循环 token 预算"
    )

    demo_p = sub.add_parser("demo", help="离线全链路演示（无 LLM 也可跑）")
    demo_p.add_argument(
        "--no-reset", action="store_true", help="不清空演示数据目录"
    )

    ext_p = sub.add_parser("extract", help="从文本文件提取认知块并入库")
    ext_p.add_argument("file", help="文本文件路径（UTF-8）")
    ext_p.add_argument("--user", default=USER_ID, help="用户标识")

    args = parser.parse_args(argv)

    if args.command == "chat":
        return cmd_chat(args)
    if args.command == "demo":
        return run_demo(reset=not args.no_reset)
    if args.command == "extract":
        asyncio.run(_extract_file(args.file, args.user))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())