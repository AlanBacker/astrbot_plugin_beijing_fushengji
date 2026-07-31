"""插件层集成测试：伪造消息事件，驱动 main.py 的完整调度管线。

需要安装 astrbot 才能运行（CI/本地无 astrbot 时自动跳过）；
force_text_mode=True 走文本兜底，同时也覆盖了上下文构建的键完整性。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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

    def __init__(
        self,
        origin: str,
        uid: str,
        name: str = "",
        admin: bool = False,
        message: str = "",
        woken: bool = False,
    ):
        self.unified_msg_origin = origin
        self._uid = uid
        self._name = name or f"玩家{uid}"
        self._admin = admin
        self.message_str = message
        self.is_at_or_wake_command = woken
        self._stopped = False

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

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped


class FakeProvider:
    """可编排的 LLM 供应商替身：script 里放 str（返回该文本）或 Exception（抛出）。"""

    def __init__(self, script, pid="fake"):
        self.script = list(script)
        self.calls = 0
        self._pid = pid

    def meta(self):
        return SimpleNamespace(id=self._pid)

    async def text_chat(self, prompt="", system_prompt="", **kw):
        self.calls += 1
        item = self.script.pop(0) if self.script else RuntimeError("剧本用尽")
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(completion_text=item)


class FakeContext:
    """只实现 _resolve_ai_providers 用到的两个查询。"""

    def __init__(self, by_id=None, using=None):
        self._by_id = by_id or {}
        self._using = using

    def get_provider_by_id(self, pid):
        return self._by_id.get(pid)

    def get_using_provider(self, umo=None):
        return self._using


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
        # 房主发车：确认文本 + 第 1 天日报（文本兜底），日报上印着京城十站表
        out = texts(run(plugin.cmd_start(ev("u1"))))
        assert "第 1 天" in out and "今日行情" in out and "群雄座次" in out
        assert "京城十站" in out and "苹果园" in out
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
        assert "这一站" in out and "1北京站" in out and "10苹果园" in out

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
        assert "玩法速览" in out and "京城十站" in out


# ---------------------------------------------------------------------------
# 输错命令兜底：唤醒词 + 未知子命令必须报错拦截，不能漏给 LLM
# ---------------------------------------------------------------------------


def wake_ev(message: str, woken: bool = True) -> FakeEvent:
    return FakeEvent("test:GroupMessage:10086", "u1", message=message, woken=woken)


class TestTypoGuard:
    @pytest.mark.parametrize("msg", ["fs 出 3 全", "浮生记 出 3 全", "浮生 出 3 全"])
    def test_typo_subcommand_errors_and_stops(self, plugin, msg):
        e = wake_ev(msg)
        out = texts(run(plugin.guard_typo(e)))
        assert "没有「出」" in out and "浮生记 卖" in out and "浮生记 帮助" in out
        assert e.is_stopped()

    def test_unrecognizable_typo_still_stops_with_help_hint(self, plugin):
        e = wake_ev("fs 梭哈 全部")
        out = texts(run(plugin.guard_typo(e)))
        assert "没有「梭哈」" in out and "浮生记 帮助" in out
        assert e.is_stopped()

    @pytest.mark.parametrize(
        "msg", ["浮生记 卖 1 全", "fs 出售 1 全", "浮生 buy 7 全", "浮生记 认输 确认"]
    )
    def test_known_subcommand_passes_silently(self, plugin, msg):
        e = wake_ev(msg)
        assert run(plugin.guard_typo(e)) == []
        assert not e.is_stopped()

    def test_not_woken_message_is_ignored(self, plugin):
        e = wake_ev("fs 出 3 全", woken=False)
        assert run(plugin.guard_typo(e)) == []
        assert not e.is_stopped()

    @pytest.mark.parametrize("msg", ["浮生记真好玩 啊", "今天 买 什么好"])
    def test_chatter_is_left_alone(self, plugin, msg):
        # 首词不是唤醒词的闲聊照旧归 LLM（裸唤醒词见 TestBareWakeWord）
        e = wake_ev(msg)
        assert run(plugin.guard_typo(e)) == []
        assert not e.is_stopped()

    def test_wrong_case_fs_redirects(self, plugin):
        e = wake_ev("FS 卖 1 全")
        out = texts(run(plugin.guard_typo(e)))
        assert "小写「fs」" in out and "浮生记 卖" in out
        assert e.is_stopped()

    def test_close_match_suggestions(self, plugin):
        assert plugin._closest_subcommand("出") == "卖"
        assert plugin._closest_subcommand("购") == "买"
        assert plugin._closest_subcommand("排") == "排行"
        assert plugin._closest_subcommand("HELP") == "帮助"
        assert plugin._closest_subcommand("龙虎") == "榜单"
        assert plugin._closest_subcommand("彩票") == ""

    def test_subcommand_table_matches_registrations(self):
        """SUBCOMMANDS 手工表必须与实际注册的指令树完全一致，防止改代码漏改表。"""
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        from astrbot.core.star.star_handler import star_handlers_registry

        registered: dict[str, tuple[str, ...]] = {}
        group_names: set[str] = set()
        for md in star_handlers_registry.get_handlers_by_module_name(plugin_main.__name__):
            for f in md.event_filters:
                if isinstance(f, CommandGroupFilter) and f._original_group_name == "浮生记":
                    group_names = {f._original_group_name, *f.alias}
                elif isinstance(f, CommandFilter) and "浮生记" in (f.parent_command_names or []):
                    registered[f._original_command_name] = tuple(sorted(f.alias))
        assert registered, "没在注册表里找到浮生记的子命令，introspection 失效"
        assert group_names == set(plugin_main.WAKE_WORDS)
        expected = {k: tuple(sorted(v)) for k, v in plugin_main.SUBCOMMANDS.items()}
        assert registered == expected


class TestBareWakeWord:
    """光发唤醒词：回带本群进度的开场引导，替代框架生硬的「参数不足」树。"""

    @pytest.mark.parametrize("word", ["浮生记", "fs", "浮生"])
    def test_no_game_intros_create(self, plugin, word):
        e = wake_ev(word)
        out = texts(run(plugin.guard_typo(e)))
        assert "浮生记 创建" in out and "浮生记 帮助" in out
        assert e.is_stopped()

    def test_signup_points_to_join_and_start(self, plugin):
        run(plugin.cmd_create(ev("u1"), "10"))
        out = texts(run(plugin.guard_typo(wake_ev("浮生记"))))
        assert "浮生记 加入" in out and "浮生记 开始" in out
        assert "1/4 人" in out  # 报名进度

    def test_running_points_to_panel(self, plugin):
        run(plugin.cmd_create(ev("u1"), "10"))
        run(plugin.cmd_start(ev("u1")))
        out = texts(run(plugin.guard_typo(wake_ev("fs"))))
        assert "第 1/10 天" in out and "浮生记 面板" in out

    def test_bare_miscased_fs_hints_lowercase(self, plugin):
        e = wake_ev("FS")
        out = texts(run(plugin.guard_typo(e)))
        assert "小写「fs」" in out and "浮生记 帮助" in out
        assert e.is_stopped()

    def test_unwoken_bare_word_ignored(self, plugin):
        e = wake_ev("浮生记", woken=False)
        assert run(plugin.guard_typo(e)) == []
        assert not e.is_stopped()

    def test_group_filter_softened_no_raise(self, plugin):
        """框架层：消息恰好等于组名/别名时按未命中放行，不再抛「参数不足」。"""
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        from astrbot.core.star.star_handler import star_handlers_registry

        gfs = [
            f
            for md in star_handlers_registry.get_handlers_by_module_name(
                plugin_main.__name__
            )
            for f in md.event_filters
            if isinstance(f, CommandGroupFilter) and f.parent_group is None
        ]
        assert gfs, "指令组过滤器应已注册"
        for gf in gfs:
            for word in plugin_main.WAKE_WORDS:
                assert gf.filter(wake_ev(word), None) is False  # 不抛、不响
            # 正常带子命令的消息照旧命中指令组
            assert gf.filter(wake_ev("浮生记 创建 10"), None) is True


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


# ---------------------------------------------------------------------------
# AI 说书人：指定供应商、累计重试、回退与失败提示
# ---------------------------------------------------------------------------


def _settle() -> "plugin_main.engine.Settlement":
    e = plugin_main.engine
    c = plugin_main.const
    return e.Settlement(
        days_total=5,
        days_played=5,
        boom_total=9,
        boom_tier=1,
        boom_label=c.BOOM_TIER_LABELS[1],
        boom_line=c.BOOM_TIER_LINES[1],
        entries=[
            e.SettleEntry(
                uid="u1", name="张三", reason="normal", score=1000,
                fame=90, fame_title="有口皆碑",
                profit=4500, boom_seen=9, days_active=5, trades=6,
                market_tier=1, market_grade=2,
                market_verdict=c.MARKET_VERDICTS[1][2],
            )
        ],
    )


class TestEpiloguePrompts:
    """说书人提示词：单人/多人两套，数值机制与行情评价标准都要交代。"""

    def test_solo_prompt(self, plugin):
        prompt, sysp = plugin._epilogue_prompts(_settle())
        assert "单人局" in sysp and "多人局" not in sysp
        assert "不超过 60 字" in sysp
        # 数值机制交代：本金、利率、身家公式
        assert "现金 2000" in sysp and "日息 10%" in sysp and "排名只看身家" in sysp
        # 公平标准：对照行情说话，垫底档留口德
        assert "冷清局赚到小钱也是本事" in sysp and "身故最低" in sysp
        # 对局数据：行情档位 + 个人评级与参考评语
        assert "行情档位「平淡」" in prompt
        assert "张三" in prompt and "系统评级「稳健」" in prompt
        assert plugin_main.const.MARKET_VERDICTS[1][2] in prompt

    def test_multi_prompt_with_debt_and_dead(self, plugin):
        c = plugin_main.const
        e = plugin_main.engine
        s = _settle()
        s.entries.append(e.SettleEntry(
            uid="u2", name="李四", reason="normal", score=-200,
            fame=80, fame_title="口碑平平",
            profit=3300, boom_seen=9, days_active=5, trades=2,
            market_tier=1, market_grade=c.GRADE_DEBT,
            market_verdict=c.DEBT_VERDICTS[1],
        ))
        s.entries.append(e.SettleEntry(
            uid="u3", name="王五", reason="dead", score=None,
            fame=60, fame_title="口碑平平",
            market_grade=c.GRADE_DEAD, market_verdict=c.DEAD_VERDICT,
        ))
        prompt, sysp = plugin._epilogue_prompts(s)
        assert "多人局" in sysp and "不超过 90 字" in sysp
        assert "以系统评级为准" in sysp
        assert "系统评级「欠债离场」" in prompt  # 李四：欠债有专门评级词
        assert "王五：身故" in prompt  # 死者单独一行，不套盈利句式


class TestSettleVerdictOutput:
    """结算页（非 AI 路径）：行情条与每人一行评语要落到用户可见输出。"""

    def test_settle_context_and_text_fallback(self):
        ctx = plugin_main.contexts.settle_context(_settle())
        assert ctx["boom_label"] == "平淡"
        assert ctx["boom_line"] == plugin_main.const.BOOM_TIER_LINES[1]
        assert ctx["boom_stat"] == "5 天累计景气 9 点"
        assert ctx["entries"][0]["verdict"] == plugin_main.const.MARKET_VERDICTS[1][2]
        txt = plugin_main.text_fallback.settle_text(ctx)
        assert "评｜" in txt and ctx["boom_line"] in txt

    def test_full_game_settlement_shows_verdicts(self, plugin):
        run(plugin.cmd_create(ev("u1"), "5"))
        run(plugin.cmd_start(ev("u1")))
        settled = ""
        for _ in range(6):
            out = texts(run(plugin.cmd_stay(ev("u1"))))
            if "最终结算" in out:
                settled = out
                break
        assert "本局行情" in settled and "累计景气" in settled
        assert "评｜" in settled


class TestAiEpilogue:
    def _run(self, plugin, ctx, pid=""):
        plugin.context = ctx
        plugin.conf["ai_provider_id"] = pid
        plugin.ai_retry_delay = 0  # 测试不真睡
        return asyncio.run(plugin._ai_epilogue(ev("u1"), _settle()))

    def test_configured_provider_first_try(self, plugin):
        good = FakeProvider(["各回各家咯"], pid="gpt")
        text = self._run(plugin, FakeContext(by_id={"gpt": good}), pid="gpt")
        assert "说书人收场白" in text and "各回各家咯" in text
        assert good.calls == 1

    def test_retries_then_falls_back_to_session_default(self, plugin):
        bad = FakeProvider([RuntimeError(f"超时{i}") for i in range(3)], pid="gpt")
        backup = FakeProvider(["买定离手"])
        text = self._run(plugin, FakeContext(by_id={"gpt": bad}, using=backup), pid="gpt")
        assert "买定离手" in text
        assert bad.calls == 3 and backup.calls == 1

    def test_all_fail_yields_explicit_notice(self, plugin):
        bad = FakeProvider([RuntimeError("炸")] * 3, pid="gpt")
        backup = FakeProvider([RuntimeError("也炸")])
        text = self._run(plugin, FakeContext(by_id={"gpt": bad}, using=backup), pid="gpt")
        assert "AI 总结失败" in text
        assert bad.calls == 3 and backup.calls == 1

    def test_unknown_id_gives_session_default_full_tries(self, plugin):
        backup = FakeProvider([RuntimeError("闪"), "尘埃落定"])
        text = self._run(plugin, FakeContext(using=backup), pid="不存在的id")
        assert "尘埃落定" in text
        assert backup.calls == 2

    def test_no_provider_at_all(self, plugin):
        text = self._run(plugin, FakeContext())
        assert "AI 总结失败" in text and "没有可用" in text

    def test_empty_completion_counts_as_failure(self, plugin):
        hollow = FakeProvider(["", "  ", ""])
        text = self._run(plugin, FakeContext(using=hollow))
        assert "AI 总结失败" in text
        assert hollow.calls == 3

    def test_settlement_emits_epilogue(self, plugin):
        """走完整局：ai_comment 开启时结算图后追加收场白。"""
        plugin.conf["ai_comment"] = True
        plugin.context = FakeContext(using=FakeProvider(["有钱没钱，回家过年"]))
        plugin.ai_retry_delay = 0
        run(plugin.cmd_create(ev("u1"), "5"))
        run(plugin.cmd_start(ev("u1")))
        settled = ""
        for _ in range(6):
            out = texts(run(plugin.cmd_stay(ev("u1"))))
            if "最终结算" in out:
                settled = out
                break
        assert "说书人收场白" in settled and "回家过年" in settled
