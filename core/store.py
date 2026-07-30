"""存档与排行榜持久化。

所有数据以 JSON 文件形式存放在 AstrBot 的 data/plugin_data/<插件名>/ 下：

    rooms/<会话散列>.json   —— 每个会话（群/私聊)一个游戏房间存档
    leaderboard.json        —— 历史高分榜（全局)

写入采用"临时文件 + os.replace"的原子方式，避免进程中断产生半截文件。
本模块只做"dict <-> 文件"，不理解游戏语义；序列化由 models 层负责。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写 JSON：写临时文件后 rename 到目标路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any] | None:
    """读 JSON；文件不存在或损坏时返回 None（损坏文件重命名保留现场）。"""
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        try:
            path.rename(path.with_suffix(path.suffix + ".corrupt"))
        except OSError:
            pass
        return None


class GameStore:
    """游戏数据的文件仓库。"""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.rooms_dir = self.base_dir / "rooms"
        self.leaderboard_path = self.base_dir / "leaderboard.json"

    # ---------- 房间存档 ----------

    @staticmethod
    def room_file_name(room_id: str) -> str:
        """把会话 ID（unified_msg_origin，含 ':' 等字符）映射为安全文件名。"""
        digest = hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:24]
        return f"room_{digest}.json"

    def _room_path(self, room_id: str) -> Path:
        return self.rooms_dir / self.room_file_name(room_id)

    def save_room(self, room_id: str, room_dict: dict[str, Any]) -> None:
        payload = {"schema": SCHEMA_VERSION, "room_id": room_id, "room": room_dict}
        _atomic_write_json(self._room_path(room_id), payload)

    def load_room(self, room_id: str) -> dict[str, Any] | None:
        payload = _load_json(self._room_path(room_id))
        if not payload or payload.get("schema") != SCHEMA_VERSION:
            return None
        if payload.get("room_id") != room_id:  # 防手工挪动/覆盖错档
            return None
        return payload.get("room")

    def delete_room(self, room_id: str) -> None:
        try:
            self._room_path(room_id).unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- 排行榜 ----------

    def load_leaderboard(self) -> list[dict[str, Any]]:
        payload = _load_json(self.leaderboard_path)
        if not payload or payload.get("schema") != SCHEMA_VERSION:
            return []
        entries = payload.get("entries")
        return entries if isinstance(entries, list) else []

    def save_leaderboard(self, entries: list[dict[str, Any]]) -> None:
        _atomic_write_json(
            self.leaderboard_path, {"schema": SCHEMA_VERSION, "entries": entries}
        )
