"""插件层集成测试：伪造消息事件，驱动 main.py 的完整调度管线。

需要安装 astrbot 才能运行（CI/本地无 astrbot 时自动跳过）；
force_text_mode=True 走文本兜底，同时也覆盖了上下文构建的键完整性。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from conftest import ScriptRng

pytest.importorskip("astrbot")

_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
try:
    from astrbot_plugin_beijing_fushengji import main as plugin_main
except ImportError:  # 插件目录被改名时无法按包名导入
    pytest.skip("插件目录名不是 astrbot_plugin_beijing_fushengji", allow_module_level=True)

from astrbot.api.star import StarTools  # noqa: E402


class FakeEvent:
    """只实现 main.py 用到的事件表面。"""

    def __init__(self, origin: str, uid: str, name: str = "", admin: bool = False):
        self.unified_msg_origin = origin
        self._uid = uid
        self._name = name or f"玩家{uid}"
        self._admin = admin

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def is_admin(self):
        return self._admin

    def plain_result(self, text: str):
        return ("plain", text)

    def image_result(self, path: str):
        return ("image", path)


def run(agen):
    """把 async generator 消费成列表。"""

    async def _collect():
        return [item async for item in agen]

    return asyncio.run(_collect())


def texts(outs):
    return "\n".join(t for kind, t in outs if kind == "plain")


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(
        StarTools, "get_data_dir", classmethod(lambda cls, name=None: tmp_path)
    )
    p = plugin_main.BeijingFushengji(context=None, config={"force_text_mode": True})
    p.rng = ScriptRng()  # 风平浪静的随机源，测试可预期
    return p


def ev(uid: str, origin: str = "test:GroupMessage:10086", admin: bool = False) -> FakeEvent:
    return FakeEvent(origin, uid, admin=admin)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_no_game_hint(self, plugin):
        out = texts(run(plugin.cmd_panel(ev("u1"))))
        assert "还没有开局" in out and "浮生记 创建" in out

    def test_create_join_start_flow(self, plugin):
        out = texts(run(plugin.cmd_create(ev("u1"), "10")))
        assert "开张" in out
        # 重复创建被拦
        out = texts(run(plugin.cmd_create(ev("u2"))))
        assert "候车" in out
        out = texts(run(plugin.cmd_join(ev("u2"))))
        assert "2/4" in out
        # 非房主不能发车
        out = texts(run(plugin.cmd_start(ev("u2"))))
        assert "房主" in out
        # 房主发车：确认文本 + 第 1 天日报（文本兜底）
        out = texts(run(plugin.cmd_start(ev("u1"))))
        assert "第 1 天" in out and "今日行情" in out and "群雄座次" in out
        # 存档已落盘：清缓存后仍能继续
        plugin.rooms.clear()
        out = texts(run(plugin.cmd_panel(ev("u1"))))
        assert "随身账本" in out or "第 1 天" in out

    def test_dissolve_needs_confirm_and_permission(self, plugin):
        run(plugin.cmd_create(ev("u1"), "10"))
        out = texts(run(plugin.cmd_dissolve(ev("u1"))))
        assert "确认" in out
        out = texts(run(plugin.cmd_dissolve(ev("u2"), "确认")))
        assert "房主或管理员" in out
        out = texts(run(plugin.cmd_dissolve(ev("u1"), "确认")))
        assert "散伙" in out
        assert plugin.store.load_room("test:GroupMessage:10086") is None

    def test_rooms_are_isolated_by_origin(self, plugin):
        run(plugin.cmd_create(ev("u1", origin="test:GroupMessage:A"), "10"))
        out = texts(run(plugin.cmd_panel(ev("u1", origin="test:GroupMessage:B"))))
        assert "还没有开局" in out


# ---------------------------------------------------------------------------
# 对局操作与结算
# ---------------------------------------------------------------------------


class TestPlayFlow:
    def _start(self, plugin, uids, days="5"):
        run(plugin.cmd_create(ev(uids[0]), days))
        for u in uids[1:]:
            run(plugin.cmd_join(ev(u)))
        run(plugin.cmd_start(ev(uids[0])))

    def test_trade_and_hints(self, plugin):
        self._start(plugin, ["u1"])
        out = texts(run(plugin.cmd_buy(ev("u1"))))
        assert "买多少件" in out
        out = texts(run(plugin.cmd_buy(ev("u1"), "化妆品", "全")))
        assert "买入" in out
        out = texts(run(plugin.cmd_sell(ev("u1"), "化妆品", "全")))
        assert "卖出" in out
        out = texts(run(plugin.cmd_deposit(ev("u1"), "半")))
        assert "存入" in out
        out = texts(run(plugin.cmd_withdraw(ev("u1"))))
        assert "取出" in out
        out = texts(run(plugin.cmd_repay(ev("u1"), "100")))
        assert "还债" in out
        out = texts(run(plugin.cmd_go(ev("u1"), "不存在的地方")))
        assert "这一站" in out

    def test_full_game_to_settlement(self, plugin):
        self._start(plugin, ["u1", "u2"], days="5")
        settled = ""
        for _ in range(6):
            texts(run(plugin.cmd_stay(ev("u1"))))
            out2 = texts(run(plugin.cmd_stay(ev("u2"))))
            if "最终结算" in out2:
                settled = out2
                break
        assert "最终结算" in settled
        # 结算后房间已删档，再操作提示重新开局
        out = texts(run(plugin.cmd_stay(ev("u1"))))
        assert "还没有开局" in out

    def test_surrender_needs_confirm(self, plugin):
        self._start(plugin, ["u1", "u2"])
        out = texts(run(plugin.cmd_surrender(ev("u1"))))
        assert "不能反悔" in out
        out = texts(run(plugin.cmd_surrender(ev("u1"), "确认")))
        assert "提前离场" in out

    def test_rank_and_board_views(self, plugin):
        self._start(plugin, ["u1"])
        out = texts(run(plugin.cmd_rank(ev("u1"))))
        assert "群雄座次" in out
        out = texts(run(plugin.cmd_board(ev("u1"))))
        assert "浮生龙虎榜" in out and "赖皮张" in out  # 空榜时展示祖传榜首
        out = texts(run(plugin.cmd_help(ev("u1"))))
        assert "玩法速览" in out


# ---------------------------------------------------------------------------
# 催场与闲置自动跳天
# ---------------------------------------------------------------------------


class TestSkipAndIdle:
    def test_skip_requires_caller_acted(self, plugin):
        run(plugin.cmd_create(ev("u1"), "10"))
        run(plugin.cmd_join(ev("u2")))
        run(plugin.cmd_start(ev("u1")))
        out = texts(run(plugin.cmd_skip(ev("u1"))))
        assert "你自己今天还没行动" in out
        texts(run(plugin.cmd_stay(ev("u1"))))
        out = texts(run(plugin.cmd_skip(ev("u1"))))
        assert "催场" in out and "第 2 天" in out

    def test_idle_auto_skip_fires_on_any_command(self, plugin):
        plugin.conf["idle_hours"] = 1e-9  # 立即超时
        run(plugin.cmd_create(ev("u1"), "10"))
        run(plugin.cmd_join(ev("u2")))
        run(plugin.cmd_start(ev("u1")))
        out = texts(run(plugin.cmd_panel(ev("u1"))))
        assert "天黑了还没动静" in out and "第 2 天" in out
