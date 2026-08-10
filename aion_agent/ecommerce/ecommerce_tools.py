"""电商工具集 —— 商品图 / 商品视频生成（Agnes，OpenAI 兼容）

提取自 AI_chat_web 的 generation_service.py + tools/media/gen.py：
- 文生图商品图（generate_product_image）
- 图生图商品图编辑（edit_product_image）
- 商品视频任务提交（submit_product_video）
- 视频生成状态查询（generation_status）

以 Skill 形式接入通用底座（skills/catalog.py），handler 同步执行
（ToolExecutor 会在后台线程运行，超时熔断）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.ecommerce import agnes_client

logger = logging.getLogger(__name__)

_IMAGE_SIZES = ("1024x768", "1024x1024", "768x1024")

# 生图/视频提交为长耗时网络调用（Agnes 官方建议客户端超时 60-360s），
# 单独放宽工具熔断阈值；其余工具沿用全局默认（120s）。
_TOOL_TIMEOUTS = {
    "generate_product_image": 300,
    "edit_product_image": 300,
    "submit_product_video": 180,
}


def _make_handlers() -> Dict[str, Callable]:
    """构建电商工具 handler（同步函数）"""

    def _generate_product_image(args: Dict[str, Any]) -> dict:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("缺少参数 prompt（商品图描述，如：白色保温杯、极简风格、纯色背景）")
        size = str(args.get("size") or "1024x768").strip()
        if size not in _IMAGE_SIZES:
            raise ValueError(f"size 仅支持：{', '.join(_IMAGE_SIZES)}")
        url = agnes_client.generate_image(prompt, size=size)
        return {"content": f"🖼 商品图已生成：{url}", "image_url": url, "mode": "text_to_image"}

    def _edit_product_image(args: Dict[str, Any]) -> dict:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("缺少参数 prompt（修改指令，如：把背景换成白色、去掉文字水印）")
        size = str(args.get("size") or "1024x768").strip()
        if size not in _IMAGE_SIZES:
            raise ValueError(f"size 仅支持：{', '.join(_IMAGE_SIZES)}")
        # 参考图：image_urls 数组（多图生图，与 web 端 image 数组传参一致）；
        # 兼容单个 image_url（单图生图）
        image_urls = args.get("image_urls") or []
        if isinstance(image_urls, str):
            image_urls = [u.strip() for u in image_urls.split(",") if u.strip()]
        image_urls = [str(u).strip() for u in image_urls if str(u).strip()]
        single = str(args.get("image_url") or "").strip()
        if single and single not in image_urls:
            image_urls.insert(0, single)
        if not image_urls:
            raise ValueError("缺少参数 image_url / image_urls（参考商品图 URL，至少 1 张）")
        url = agnes_client.generate_image(prompt, size=size, ref_images=image_urls)
        mode = "image_to_image" if len(image_urls) == 1 else "multi_image_to_image"
        return {
            "content": f"🎨 商品图已生成：{url}",
            "image_url": url,
            "mode": mode,
            "ref_image_count": len(image_urls),
        }

    def _submit_product_video(args: Dict[str, Any]) -> dict:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("缺少参数 prompt（商品视频描述，如：保温杯 360 度旋转展示）")
        images = str(args.get("image_urls") or "").strip()
        image_urls = [u.strip() for u in images.split(",") if u.strip()] or None
        result = agnes_client.submit_video(
            prompt,
            image_urls=image_urls,
            num_frames=int(args.get("num_frames") or 121),
            frame_rate=int(args.get("frame_rate") or 24),
        )
        return {
            "content": (
                f"🎬 商品视频任务已提交：task_id={result['task_id']}，"
                f"video_id={result['video_id']}。生成完成后用 generation_status 查询结果。"
            ),
            **result,
        }

    def _generation_status(args: Dict[str, Any]) -> dict:
        video_id = str(args.get("video_id") or "").strip()
        if not video_id:
            raise ValueError("缺少参数 video_id（submit_product_video 返回的 video_id）")
        status = agnes_client.get_video_status(video_id)
        url = (status.get("url") or status.get("video_url")
               or (status.get("output") or {}).get("url") or "")
        progress = status.get("progress")
        text = f"视频状态：{status.get('status') or '未知'}"
        if progress is not None:
            text += f"（进度 {progress}%）"
        if url:
            text += f"\n🎬 视频地址：{url}"
        return {"content": text, "video_url": url, "status": status}

    return {
        "generate_product_image": _generate_product_image,
        "edit_product_image": _edit_product_image,
        "submit_product_video": _submit_product_video,
        "generation_status": _generation_status,
    }


# ==================== OpenAI 格式 schema ====================

_ECOMMERCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_product_image",
            "description": "【电商】文生图：根据商品描述生成商品图片（主图/概念图），返回图片 URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "商品图描述，如：白色保温杯、极简风格、纯色背景"},
                    "size": {"type": "string", "description": "图片尺寸：1024x768 / 1024x1024 / 768x1024（默认 1024x768）"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_product_image",
            "description": "【电商】图生图/多图生图：基于 1 张或多张参考商品图按指令修改（换背景/改风格/去水印/调整局部细节），返回图片 URL。参考图来自用户上传或历史消息中的【用户上传的参考图】列表；除非用户明确要求新视角/新构图（如生成三视图），否则保持原图视角与构图，只按指令修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "修改指令，如：把背景换成白色、去掉文字水印"},
                    "image_url": {"type": "string", "description": "参考商品图 URL（单图时使用；与 image_urls 二选一）"},
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参考商品图 URL 数组（多图生图；与 image_url 二选一，至少 1 张）",
                    },
                    "size": {"type": "string", "description": "图片尺寸：1024x768 / 1024x1024 / 768x1024（默认 1024x768）"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_product_video",
            "description": "【电商】提交商品视频生成任务（可基于商品图），返回 task_id/video_id，异步生成",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "商品视频描述，如：保温杯 360 度旋转展示"},
                    "image_urls": {"type": "string", "description": "参考图 URL，多个用逗号分隔（可选）"},
                    "num_frames": {"type": "integer", "description": "总帧数（默认 121）"},
                    "frame_rate": {"type": "integer", "description": "帧率（默认 24）"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generation_status",
            "description": "【电商】查询商品视频生成状态与结果地址（video_id 来自 submit_product_video）",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {"type": "string", "description": "submit_product_video 返回的 video_id"},
                },
                "required": ["video_id"],
            },
        },
    },
]


def register_ecommerce_tools(
    registry: IToolRegistry,
    user_id: str = "chat_user",
) -> None:
    """注册 4 个电商工具（handler 与 schema 成对注册，T2 技能层）"""
    handlers = _make_handlers()
    for tool in _ECOMMERCE_TOOLS:
        name = tool["function"]["name"]
        registry.register(
            name,
            handlers[name],
            schema=tool,
            level="skill",
            timeout_seconds=_TOOL_TIMEOUTS.get(name),
        )

