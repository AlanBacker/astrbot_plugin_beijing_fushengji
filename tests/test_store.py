"""存储层测试：原子写、损坏兜底、房间与排行榜读写。"""

from __future__ import annotations

import json

from core import engine
from core.models import Room
from core.store import GameStore


def _room_dict(room_id="s1"):
    return engine.create_room(room_id, "u0", "甲", 40, {}, 0.0).to_dict()


def test_room_roundtrip(tmp_path):
    store = GameStore(tmp_path)
    d = _room_dict("qq:group:123")
    store.save_room("qq:group:123", d)
    loaded = store.load_room("qq:group:123")
    assert loaded == d
    assert Room.from_dict(loaded).to_dict() == d
    assert store.load_room("qq:group:999") is None


def test_delete_room(tmp_path):
    store = GameStore(tmp_path)
    store.save_room("s1", _room_dict())
    store.delete_room("s1")
    assert store.load_room("s1") is None
    store.delete_room("s1")  # 幂等


def test_corrupt_file_quarantined(tmp_path):
    store = GameStore(tmp_path)
    store.save_room("s1", _room_dict())
    path = store._room_path("s1")
    path.write_text("{broken json", encoding="utf-8")
    assert store.load_room("s1") is None
    assert len(list(path.parent.glob("*.corrupt"))) == 1
    # 隔离后可以重新建档
    store.save_room("s1", _room_dict())
    assert store.load_room("s1") is not None


def test_room_id_mismatch_rejected(tmp_path):
    store = GameStore(tmp_path)
    store.save_room("s1", _room_dict())
    path = store._room_path("s1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["room_id"] = "s2"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert store.load_room("s1") is None


def test_wrong_schema_rejected(tmp_path):
    store = GameStore(tmp_path)
    store.save_room("s1", _room_dict())
    path = store._room_path("s1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = 999
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert store.load_room("s1") is None


def test_leaderboard_roundtrip(tmp_path):
    store = GameStore(tmp_path)
    assert store.load_leaderboard() == []
    board = [{"name": "甲", "score": 100, "days": 40, "room": "群", "ts": 1.0}]
    store.save_leaderboard(board)
    assert store.load_leaderboard() == board


def test_atomic_write_leaves_no_tmp(tmp_path):
    store = GameStore(tmp_path)
    for _ in range(5):
        store.save_room("s1", _room_dict())
    stray = [p for p in store.rooms_dir.iterdir() if not p.name.endswith(".json")]
    assert stray == []


def test_filename_is_filesystem_safe(tmp_path):
    store = GameStore(tmp_path)
    weird = "aiocqhttp:GroupMessage:12345/../..\\x"
    store.save_room(weird, _room_dict(weird))
    assert store.load_room(weird) is not None
    files = list(store.rooms_dir.iterdir())
    assert len(files) == 1 and files[0].name.startswith("room_")
