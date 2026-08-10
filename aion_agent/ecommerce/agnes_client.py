"""Agnes 生成服务客户端 —— 提取自 AI_chat_web/generation_service.py

能力：文生图 / 图生图（参考图数组）/ 视频任务提交与状态查询。
配置（环境变量）：
  AGNES_API_KEY   必填，Bearer 鉴权（与 AI_chat_web 共用同一个 key）
  AGNES_BASE_URL  可选，默认 https://apihub.agnes-ai.com/v1
"""

from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"
_REQUEST_TIMEOUT = 300  # 秒；Agnes 官方建议客户端超时 60-360s（多图生图耗时更长）


_REF_IMAGE_MAX_BYTES = 4 * 1024 * 1024  # 本地参考图超过该大小则压缩后再 base64
_REF_IMAGE_MAX_DIM = 2048  # 压缩后最长边（像素）
_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _uploads_root() -> Path:
    """本地上传目录：与 server.runtime 的数据目录约定保持一致"""
    override = os.environ.get("AION_DATA_DIR")
    root = Path(override).expanduser() if override else Path.home() / ".aion_agent"
    return root / "server" / "uploads"


def _file_to_data_uri(path: Path) -> str:
    """读取本地图片为 base64 Data URI（过大时用 PIL 压缩）"""
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = raw
    if len(raw) > _REF_IMAGE_MAX_BYTES:
        compressed = _try_compress_image(raw, path)
        if compressed is not None and len(compressed[0]) < len(raw):
            data, mime = compressed
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _try_compress_image(raw: bytes, path: Path) -> Optional[tuple]:
    """PIL 压缩：限制最长边 + 重编码；失败返回 None（退回原始字节）"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.load()
        if max(img.size) > _REF_IMAGE_MAX_DIM:
            img.thumbnail((_REF_IMAGE_MAX_DIM, _REF_IMAGE_MAX_DIM), Image.LANCZOS)
        fmt = (img.format or "PNG").upper()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        buf = io.BytesIO()
        if fmt in ("JPEG", "JPG"):
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, "JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg"
        if fmt == "PNG":
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            if has_alpha:
                img.save(buf, "PNG", optimize=True)
                return buf.getvalue(), "image/png"
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, "JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg"
        kwargs = {"optimize": True} if fmt in ("WEBP", "GIF") else {}
        img.save(buf, fmt, **kwargs)
        return buf.getvalue(), mime
    except Exception:  # noqa: BLE001
        logger.warning("本地参考图压缩失败，改用原始文件", exc_info=True)
        return None


def _resolve_image_ref(ref: str) -> str:
    """把参考图参数转成 Agnes 服务端可访问的形式。

    - /uploads/xxx 或 http(s)://127.0.0.1|localhost[:端口]/uploads/xxx：本地文件 → base64 Data URI
    - 本机存在的绝对路径：→ base64 Data URI
    - data: URI / 公网 http(s) URL：原样返回
    """
    ref = (ref or "").strip()
    if not ref or ref.startswith("data:"):
        return ref
    path: Optional[Path] = None
    if ref.startswith("/uploads/"):
        path = _uploads_root() / ref[len("/uploads/"):]
    elif ref.startswith("http://") or ref.startswith("https://"):
        parts = urlsplit(ref)
        if parts.hostname in _LOCALHOST_HOSTS and parts.path.startswith("/uploads/"):
            path = _uploads_root() / parts.path[len("/uploads/"):]
    else:
        candidate = Path(ref)
        if candidate.is_file():
            path = candidate
    if path is None or not path.is_file():
        if path is not None:
            logger.warning("参考图本地文件不存在，按原值传递: %s", path)
        return ref
    return _file_to_data_uri(path)


def _redact_data_uri(value: Any) -> Any:
    """日志脱敏：base64 数据体只保留长度，避免刷屏"""
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        head, tail = value.split(";base64,", 1)
        return f"{head};base64,[{len(tail)} chars]"
    return value


def _safe_payload(payload: dict) -> dict:
    return {
        key: [_redact_data_uri(item) for item in value]
        if isinstance(value, list)
        else _redact_data_uri(value)
        for key, value in payload.items()
    }

def _http_request(
    url: str,
    headers: Optional[dict] = None,
    payload: Optional[dict] = None,
    timeout: int = _REQUEST_TIMEOUT,
) -> tuple:
    """标准库 HTTP 请求（无 requests 依赖，兼容 Android Chaquopy 打包）

    Returns: (status_code, response_text)；HTTP 错误返回状态码与响应体，
    网络错误（超时/连接失败）抛 AgnesError。
    """
    req_headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=body,
        headers=req_headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:  # noqa: BLE001
            pass
        return e.code, raw.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise AgnesError(f"Agnes 网络请求失败: {e.reason}") from e


def _json_or_empty(text: str) -> dict:
    """解析 JSON 响应体，解析失败返回空 dict（与 requests.json 语义对齐）"""
    try:
        data = json.loads(text or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


class AgnesError(Exception):
    """Agnes API 调用失败（配置缺失 / HTTP 错误 / 业务错误）"""


def _api_key() -> str:
    key = os.getenv("AGNES_API_KEY", "").strip()
    if not key:
        raise AgnesError("AGNES_API_KEY 未配置：请设置环境变量 AGNES_API_KEY")
    return key


def _base_url() -> str:
    return os.getenv("AGNES_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")


def _status_base_url() -> str:
    """视频状态查询端点位于根路径（不带 /v1）：https://apihub.agnes-ai.com/agnesapi"""
    base = _base_url()
    return base[: -len("/v1")] if base.endswith("/v1") else base


def generate_image(
    prompt: str,
    size: str = "1024x768",
    ref_images: Optional[List[str]] = None,
) -> str:
    """文生图 / 图生图，返回图片 URL（同步，超时 120s）。

    ref_images：参考图 URL 数组，传入则为图生图（参考图放入 extra_body.image）。
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    payload: Dict = {
        "model": IMAGE_MODEL,
        "prompt": str(prompt or "").strip(),
        "size": size or "1024x768",
        "extra_body": {"response_format": "url"},
    }
    if ref_images:
        resolved = [
            _resolve_image_ref(str(u))
            for u in ref_images
            if str(u).strip()
        ]
        if resolved:
            payload["extra_body"]["image"] = resolved
    logger.info("Agnes 图片生成 payload: %s", _safe_payload(payload))
    status, text = _http_request(
        f"{_base_url()}/images/generations",
        headers=headers,
        payload=payload,
        timeout=_REQUEST_TIMEOUT,
    )
    if status != 200:
        raise AgnesError(f"Agnes 图片生成失败 HTTP {status}: {text[:300]}")
    data = _json_or_empty(text)
    if "error" in data:
        raise AgnesError(f"Agnes 返回错误: {data['error']}")
    url = (data.get("data") or [{}])[0].get("url", "")
    if not url:
        raise AgnesError("Agnes 未返回图片 URL")
    return url


def submit_video(
    prompt: str,
    image_url: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    num_frames: int = 121,
    frame_rate: int = 24,
    width: int = 1152,
    height: int = 768,
) -> dict:
    """提交视频生成任务（异步），返回 {task_id, video_id}。"""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    payload: Dict = {
        "model": VIDEO_MODEL,
        "prompt": str(prompt or "").strip(),
        "height": int(height),
        "width": int(width),
        "num_frames": int(num_frames),
        "frame_rate": int(frame_rate),
    }
    if image_url:
        payload["image"] = _resolve_image_ref(str(image_url))
    if image_urls:
        payload["extra_body"] = {
            "image": [
                _resolve_image_ref(str(u))
                for u in image_urls
                if str(u).strip()
            ]
        }
    logger.info("Agnes 视频生成 payload: %s", _safe_payload(payload))
    status, text = _http_request(
        f"{_base_url()}/videos",
        headers=headers,
        payload=payload,
        timeout=_REQUEST_TIMEOUT,
    )
    if status != 200:
        raise AgnesError(f"Agnes 视频提交失败 HTTP {status}: {text[:300]}")
    data = _json_or_empty(text)
    task_id = data.get("task_id") or data.get("id")
    video_id = data.get("video_id")
    if not task_id or not video_id:
        raise AgnesError(f"Agnes 视频提交未返回任务信息: {str(data)[:300]}")
    return {"task_id": task_id, "video_id": video_id}


def get_video_status(video_id: str) -> dict:
    """按 video_id 查询视频生成状态（正确端点：/agnesapi?video_id=...）"""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    url = f"{_status_base_url()}/agnesapi?video_id={video_id}"
    status, text = _http_request(url, headers=headers, timeout=_REQUEST_TIMEOUT)
    if status != 200:
        raise AgnesError(f"Agnes 视频状态查询失败 HTTP {status}: {text[:300]}")
    return _json_or_empty(text)
