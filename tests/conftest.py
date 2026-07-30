"""测试公共设施。"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import engine  # noqa: E402


class ScriptRng(random.Random):
    """脚本化随机数：按队列依次弹出 randrange 的返回值。

    队列耗尽后返回 default（默认 1——1 % freq != 0，可"错过"所有
    freq>1 的事件判定，构成一个"风平浪静"的随机源）。
    """

    def __init__(self, values: list[int] | tuple[int, ...] = (), default: int = 1):
        super().__init__(0)
        self.queue = list(values)
        self.default = default
        self.calls: list[tuple[int, int]] = []

    def randrange(self, start, stop=None, step=1, **_kw):  # type: ignore[override]
        n = start if stop is None else stop - start
        if self.queue:
            v = self.queue.pop(0)
        else:
            v = min(self.default, n - 1)
        assert 0 <= v < n, f"脚本值 {v} 超出 randrange({n}) 范围"
        self.calls.append((n, v))
        return v


def quiet_rng() -> ScriptRng:
    """永远错过所有事件的随机源。"""
    return ScriptRng()


@pytest.fixture()
def rng() -> ScriptRng:
    return quiet_rng()


def make_room(n_players: int = 1, days: int = 20, settings: dict | None = None):
    """建房并开始：返回 (room, rng)。使用安静随机源，价格恒为 base+1。"""
    r = quiet_rng()
    settings = settings if settings is not None else {}
    room = engine.create_room("test:room", "u0", "甲", days, settings, now_ts=1000.0)
    names = ["乙", "丙", "丁"]
    for i in range(1, n_players):
        engine.join_room(room, f"u{i}", names[i - 1])
    engine.start_game(room, r, "u0", now_ts=1000.0)
    return room, r


def everyone_stays(room, r):
    """所有可行动玩家留守，推动一天。返回最后一次 move 的 ActionResult。"""
    result = None
    for p in list(room.active_players()):
        if not p.moved:
            result = engine.move(room, r, p.uid, None, now_ts=2000.0)
    return result
