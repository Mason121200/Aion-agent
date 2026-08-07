"""跨设备数据共享 —— 导出 / 合并 / 拉取 Bundle

Bundle = 一份可迁移的数据快照，包含：
  cognitive.json / chat.json / study.json / plans.json / skills.json / tool_policy.json

- build_bundle(data_dir)：把本地数据文件打包成可迁移快照
- merge_bundle(data_dir, bundle)：把远程快照合并进本地（按 id + 时间戳，新值优先）
- pull_bundle(url)：从另一台设备的 /api/sync/export 拉取快照
- get_device_id(data_dir)：本机唯一标识（首次生成后持久化）

同步原则（数据主权）：
- 导出是原始 JSON 文件，用户随时可拿走、可解析、可迁移
- 合并按记录 id 去重，updated_at / created_at 新者优先，幂等可重放
- 执行日志 / 设备标识等设备专属数据不同步
"""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

BUNDLE_SCHEMA_VERSION = "1.0"

# 参与同步的数据文件（都是 id 可去重的 JSON）
SYNC_FILES = [
    "cognitive.json",
    "chat.json",
    "study.json",
    "plans.json",
    "skills.json",
    "tool_policy.json",
]

# 按 id 键控的字典文件（study/plans/skills/tool_policy/chat 会话层）
_DICT_FILES = {"study.json", "plans.json", "skills.json", "tool_policy.json", "chat.json"}
# 列表文件，元素带 id 字段（cognitive）
_LIST_FILES = {"cognitive.json"}

_ID_FIELDS = {
    "triples": "rel_id",
    "states": "state_id",
    "notes": "note_id",
    "correction_log": None,  # 无稳定 id，按内容+时间去重
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_device_id(data_dir: Path) -> str:
    device_file = Path(data_dir) / "device.json"
    try:
        if device_file.exists():
            data = json.loads(device_file.read_text(encoding="utf-8"))
            if data.get("device_id"):
                return str(data["device_id"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取设备标识失败: {e}")
    device_id = f"device_{uuid.uuid4().hex[:8]}"
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        device_file.write_text(
            json.dumps({"device_id": device_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"写入设备标识失败: {e}")
    return device_id


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 {path.name} 失败: {e}")
        return None


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------- 导出 ----------

def build_bundle(data_dir: Path) -> dict:
    files: Dict[str, dict] = {}
    for name in SYNC_FILES:
        data = _read_json(Path(data_dir) / name)
        if data is not None:
            files[name] = data
    return {
        "schema": BUNDLE_SCHEMA_VERSION,
        "device_id": get_device_id(data_dir),
        "exported_at": _now_iso(),
        "files": files,
    }


# ---------- 合并 ----------

def _newer(a: Optional[str], b: Optional[str]) -> bool:
    """a 是否比 b 新（ISO 字符串字典序可比较）"""
    if not a:
        return False
    if not b:
        return True
    return str(a) >= str(b)


def _merge_dict_file(existing: Optional[dict], incoming: dict) -> dict:
    """按顶层 id 合并字典文件：incoming 键有更新时间则新值优先，否则保留现有"""
    result = dict(existing or {})
    for key, value in (incoming or {}).items():
        if key not in result:
            result[key] = value
            continue
        old = result[key]
        if isinstance(old, dict) and isinstance(value, dict):
            in_ts = value.get("updated_at") or value.get("created_at")
            old_ts = old.get("updated_at") or old.get("created_at")
            if in_ts and _newer(in_ts, old_ts):
                result[key] = value
        elif isinstance(old, list) and isinstance(value, list):
            result[key] = _merge_message_list(old, value)
        else:
            # 无时间戳的简单值：source 覆盖（技能/策略配置）
            result[key] = value
    return result


def _merge_message_list(existing: list, incoming: list) -> list:
    """按消息 id 去重合并（保留时间序）"""
    seen = {m.get("id") for m in existing if m.get("id")}
    merged = list(existing)
    for m in incoming or []:
        mid = m.get("id")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        merged.append(m)
    merged.sort(key=lambda m: m.get("created_at") or "", reverse=False)
    return merged


def _merge_list_file(existing: Optional[dict], incoming: dict) -> dict:
    """合并 cognitive.json（列表 + id 字段）"""
    result = dict(existing or {})
    for section, id_field in _ID_FIELDS.items():
        new_items = list(result.get(section) or [])
        if id_field:
            old_items = {
                it.get(id_field): it for it in new_items if it.get(id_field)
            }
            seen = set(old_items.keys())
            for item in incoming.get(section) or []:
                item_id = item.get(id_field)
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)
                new_items.append(item)
        else:
            # correction_log：无稳定 id，按 (content, ts) 去重
            seen_keys = {
                (it.get("content"), it.get("ts")) for it in new_items
            }
            for item in incoming.get(section) or []:
                key = (item.get("content"), item.get("ts"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                new_items.append(item)
        result[section] = new_items
    return result


def merge_bundle(data_dir: Path, bundle: dict) -> dict:
    """把远程 bundle 合并进本地，返回统计"""
    files = (bundle or {}).get("files") or {}
    counts: Dict[str, object] = {
        "schema": (bundle or {}).get("schema"),
        "from_device": (bundle or {}).get("device_id"),
        "files": [],
        "total": 0,
    }
    for name in SYNC_FILES:
        if name not in files:
            continue
        target = _read_json(Path(data_dir) / name)
        if name in _LIST_FILES:
            merged = _merge_list_file(target, files[name])
        else:
            merged = _merge_dict_file(target, files[name])
        if merged != target:
            _write_json_atomic(Path(data_dir) / name, merged)
        counts["files"].append(name)  # type: ignore[union-attr]
    counts["total"] = len(counts["files"])  # type: ignore[union-attr]
    return counts


# ---------- 拉取 ----------

def pull_bundle(url: str, timeout: int = 30) -> dict:
    """从远程设备 /api/sync/export 拉取 bundle"""
    url = str(url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("同步地址需以 http:// 或 https:// 开头")
    if not url.endswith("/api/sync/export"):
        url = url + "/api/sync/export"
    req = urllib.request.Request(url, headers={"User-Agent": "AionAgent-Sync/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or "files" not in data:
        raise ValueError("远程返回的不是有效的数据包")
    return data
