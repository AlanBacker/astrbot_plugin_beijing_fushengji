"""引擎测试：房间生命周期、交易设施、每日事件流水线、结算与排行榜。"""

from __future__ import annotations

import json
import random

import pytest
from conftest import ScriptRng, everyone_stays, make_room, quiet_rng

from core import const, engine
from core.errors import GameError
from core.models import (
    FIN_DEAD,
    FIN_NORMAL,
    FIN_SURRENDER,
    ST_ACTIVE,
    ST_FINISHED,
    ST_HOSPITAL,
    Holding,
    Player,
    Room,
)


class TestLifecycle:
    def test_create_and_opening_interest(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        assert p.cash == 2000
        assert p.debt == 5500  # 开局立即结息：5000 + 10%
        assert p.health == 100 and p.fame == 100 and p.capacity == 100
        assert room.day == 1
        assert room.phase == "running"

    def test_join_rules(self):
        r = quiet_rng()
        room = engine.create_room("t", "u0", "甲", 40, {}, 0.0)
        engine.join_room(room, "u1", "乙")
        with pytest.raises(GameError):
            engine.join_room(room, "u1", "乙")  # 重复加入
        engine.join_room(room, "u2", "丙")
        engine.join_room(room, "u3", "丁")
        with pytest.raises(GameError):
            engine.join_room(room, "u4", "戊")  # 满 4 人
        with pytest.raises(GameError):
            engine.start_game(room, r, "u1", 0.0)  # 非房主
        engine.start_game(room, r, "u0", 0.0)
        with pytest.raises(GameError):
            engine.join_room(room, "u5", "己")  # 开局后不能加入
        with pytest.raises(GameError):
            engine.start_game(room, r, "u0", 0.0)  # 重复开始

    def test_days_bounds(self):
        with pytest.raises(GameError):
            engine.create_room("t", "u0", "甲", const.MIN_DAYS - 1, {}, 0.0)
        with pytest.raises(GameError):
            engine.create_room("t", "u0", "甲", const.MAX_DAYS + 1, {}, 0.0)


class TestTrade:
    def test_buy_and_sell_math(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        room.prices = [0] * const.N_GOODS
        room.prices[2] = 10  # 盗版VCD
        res = engine.buy(room, "u0", 2, 100)
        assert p.cash == 1000 and p.inventory[2].qty == 100
        assert p.inventory[2].avg_cost == 10
        assert "买入" in res.lines[0]

        room.prices[2] = 40
        res = engine.sell(room, "u0", 2, None)  # 全卖
        assert p.cash == 1000 + 4000
        assert 2 not in p.inventory
        assert "盈利 3,000" in res.lines[1]
        assert p.stats.trades == 2

    def test_buy_all_capped_by_capacity(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 10**6
        room.prices[2] = 10
        engine.buy(room, "u0", 2, None)
        assert p.used_capacity() == p.capacity == 100

    def test_buy_rejections(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        room.prices = [0] * const.N_GOODS
        with pytest.raises(GameError):
            engine.buy(room, "u0", 0, 1)  # 未上市
        room.prices[0] = 300
        with pytest.raises(GameError):
            engine.buy(room, "u0", 0, 0)  # 数量非正
        with pytest.raises(GameError):
            engine.buy(room, "u0", 0, 7)  # 现金不足（2000 < 2100）
        p.cash = 3
        with pytest.raises(GameError):
            engine.buy(room, "u0", 0, None)  # 一件也买不起

    def test_sell_rejections(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        with pytest.raises(GameError):
            engine.sell(room, "u0", 0, 1)  # 没货
        p.inventory[0] = Holding(qty=5, avg_cost=100)
        with pytest.raises(GameError):
            engine.sell(room, "u0", 0, 6)  # 超持仓
        room.prices[0] = 0
        with pytest.raises(GameError):
            engine.sell(room, "u0", 0, 1)  # 当日无行情

    def test_avg_cost_weighted(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 10**6
        p.capacity = 140
        room.prices[0] = 100
        engine.buy(room, "u0", 0, 10)
        room.prices[0] = 400
        engine.buy(room, "u0", 0, 10)
        assert p.inventory[0].avg_cost == (100 * 10 + 400 * 10) // 20

    def test_shady_goods_cost_fame_per_transaction(self):
        room, _ = make_room(1, settings={"market_impact": False})
        p = room.players["u0"]
        p.inventory[3] = Holding(qty=10, avg_cost=0)  # 假白酒
        p.inventory[4] = Holding(qty=10, avg_cost=0)  # 假古董
        room.prices[3] = 2000
        room.prices[4] = 8000
        engine.sell(room, "u0", 3, 1)
        engine.sell(room, "u0", 3, 1)
        assert p.fame == 100 - 10 - 10
        engine.sell(room, "u0", 4, 5)
        assert p.fame == 80 - 7
        assert p.stats.sold_shady is True
        assert p.stats.gift_profit == 2000 * 2 + 8000 * 5

    def test_fame_floor_zero(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.fame = 4
        p.inventory[4] = Holding(qty=1, avg_cost=0)
        room.prices[4] = 6000
        engine.sell(room, "u0", 4, 1)
        assert p.fame == 0


class TestBankAndDebt:
    def test_deposit_withdraw_repay(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        engine.deposit(room, "u0", 1500)
        assert (p.cash, p.bank) == (500, 1500)
        engine.withdraw(room, "u0", None)
        assert (p.cash, p.bank) == (2000, 0)
        engine.repay(room, "u0", 1000)
        assert (p.cash, p.debt) == (1000, 4500)
        with pytest.raises(GameError):
            engine.deposit(room, "u0", 5000)
        with pytest.raises(GameError):
            engine.withdraw(room, "u0", 1)
        with pytest.raises(GameError):
            engine.repay(room, "u0", 99999)  # 超过现金

    def test_repay_capped_by_debt_and_taunt(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 10000
        res = engine.repay(room, "u0", None)  # 全还：min(现金, 债务) = 5500
        assert p.debt == 0 and p.cash == 4500
        assert p.stats.ever_debt_free is True
        assert len(res.lines) == 2  # 带村长嘲讽
        with pytest.raises(GameError):
            engine.repay(room, "u0", 100)  # 已无债

    def test_daily_interest_floor(self):
        room, r = make_room(1)
        p = room.players["u0"]
        engine.deposit(room, "u0", 1999)
        everyone_stays(room, r)
        assert p.debt == 5500 + 550
        assert p.bank == 1999 + 19  # 1% 向下取整
        everyone_stays(room, r)
        assert p.debt == 6050 + 605


class TestFacilities:
    def test_heal(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        with pytest.raises(GameError):
            engine.heal(room, "u0", 1)  # 满血
        p.health = 90
        p.cash = 3500 * 3
        with pytest.raises(GameError):
            engine.heal(room, "u0", 11)  # 超过缺口
        engine.heal(room, "u0", None)  # 全治：min(缺口10, 买得起3) = 3
        assert p.health == 93 and p.cash == 0
        with pytest.raises(GameError):
            engine.heal(room, "u0", None)  # 一点也治不起

    def test_house_upgrade_scam_flat(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 30000  # cash <= 30000 分支：报价两万，实扣 25000
        engine.upgrade_house(room, "u0")
        assert p.capacity == 110 and p.cash == 5000

    def test_house_upgrade_scam_half(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 100000  # cash // 2 - 2000 = 48000
        engine.upgrade_house(room, "u0")
        assert p.capacity == 110 and p.cash == 48000

    def test_house_upgrade_limits(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        with pytest.raises(GameError):
            engine.upgrade_house(room, "u0")  # 现金不足 30000
        p.cash = 10**6
        for _ in range(4):
            engine.upgrade_house(room, "u0")
        assert p.capacity == const.MAX_CAPACITY
        with pytest.raises(GameError):
            engine.upgrade_house(room, "u0")  # 已到上限

    def test_cyber_cafe(self):
        room, r = make_room(1)
        p = room.players["u0"]
        start_cash = p.cash
        res = engine.cyber_cafe(room, r, "u0")
        assert p.cash == start_cash + 2  # 安静随机源：1 + 1
        assert "1/3" in res.lines[1]
        engine.cyber_cafe(room, r, "u0")
        engine.cyber_cafe(room, r, "u0")
        with pytest.raises(GameError):
            engine.cyber_cafe(room, r, "u0")  # 每局限 3 次
        p.stats.cafe_times = 0
        p.cash = 10
        with pytest.raises(GameError):
            engine.cyber_cafe(room, r, "u0")  # 低于最低消费

    def test_intel(self):
        room, r = make_room(1, days=20)
        p = room.players["u0"]
        res = engine.buy_intel(room, r, "u0")
        assert p.cash == 2000 - const.INTEL_DEFAULT_PRICE
        assert room.tip is not None
        assert p.stats.intel_times == 1
        assert any("消息" in ln for ln in res.lines)
        with pytest.raises(GameError):
            engine.buy_intel(room, r, "u0")  # 每天一次
        room.day = room.days_total  # 最后一天没有"明天"
        p.intel_day = 0
        with pytest.raises(GameError):
            engine.buy_intel(room, r, "u0")


class TestMoveAndTurn:
    def test_move_validation(self):
        room, r = make_room(1)
        with pytest.raises(GameError):
            engine.move(room, r, "u0", 0, 0.0)  # 原地"去"
        with pytest.raises(GameError):
            engine.move(room, r, "u0", 99, 0.0)  # 不存在

    def test_locked_after_move_multiplayer(self):
        room, r = make_room(2)
        engine.move(room, r, "u0", 3, 0.0)
        for fn, args in [
            (engine.buy, (room, "u0", 2, 1)),
            (engine.sell, (room, "u0", 2, 1)),
            (engine.deposit, (room, "u0", 1)),
            (engine.heal, (room, "u0", 1)),
            (engine.upgrade_house, (room, "u0")),
        ]:
            with pytest.raises(GameError):
                fn(*args)
        with pytest.raises(GameError):
            engine.move(room, r, "u0", 4, 0.0)  # 不能再动

    def test_day_advances_when_all_moved(self):
        room, r = make_room(2)
        res1 = engine.move(room, r, "u0", 3, 0.0)
        assert not res1.day_reports
        assert "还差 1 人" in res1.lines[-1]
        assert room.day == 1
        res2 = engine.move(room, r, "u1", None, 5000.0)
        assert len(res2.day_reports) == 1
        assert room.day == 2
        assert room.players["u0"].location == 3
        assert room.players["u1"].location == const.START_LOCATION
        assert room.day_started_at == 5000.0
        assert not room.players["u0"].moved

    def test_skip_idlers(self):
        room, r = make_room(3)
        engine.move(room, r, "u0", 3, 0.0)
        with pytest.raises(GameError):
            engine.skip_idlers(room, r, "u1", False, 0.0)  # 非房主
        res = engine.skip_idlers(room, r, "u0", False, 0.0)
        assert room.day == 2
        assert res.day_reports
        with pytest.raises(GameError):
            engine.skip_idlers(room, r, "u0", False, 0.0)  # 无人可跳


class TestDailyPipeline:
    def test_gift_event(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        room.prices[1] = 100  # 安静随机源会把 1 号货退市；送礼要求当日有行情
        engine._roll_gifts(room, ScriptRng([0]), p, ev := [])
        assert p.inventory[1].qty == 2 and p.inventory[1].avg_cost == 0
        assert len(ev) == 1

    def test_gift_truncated_by_capacity(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.inventory[2] = Holding(qty=97, avg_cost=10)
        engine._roll_gifts(room, ScriptRng([1, 0]), p, ev := [])  # 香烟 +6 -> 只装 3
        assert p.inventory[0].qty == 3
        assert "只塞得下" in ev[0]

    def test_gift_house_full_breaks(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.inventory[2] = Holding(qty=100, avg_cost=10)
        engine._roll_gifts(room, ScriptRng([0, 0, 0, 0]), p, ev := [])
        assert ev == [const.GIFT_HOUSE_FULL_TEXT]  # 命中即断，后续不再判定

    def test_village_phone_debt_even_when_full(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.inventory[2] = Holding(qty=100, avg_cost=10)
        engine._roll_gifts(room, ScriptRng([1, 1, 1, 0]), p, ev := [])
        assert p.debt == 5500 + 2500  # 货不给、债照加
        assert 6 not in p.inventory

    def test_gift_dilutes_avg_cost(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        room.prices[1] = 100  # 同上：先把 1 号货摆回行情
        p.inventory[1] = Holding(qty=2, avg_cost=20000)
        engine._roll_gifts(room, ScriptRng([0]), p, [])
        assert p.inventory[1].qty == 4
        assert p.inventory[1].avg_cost == 20000 * 2 // 4

    def test_gift_skipped_when_good_off_market(self):
        """原版：商品当日无行情，白捡事件不发生（判定命中也作废）。"""
        room, _ = make_room(1)
        p = room.players["u0"]
        room.prices[const.GIFT_EVENTS[0].good] = 0
        engine._roll_gifts(room, ScriptRng([0]), p, ev := [])
        assert ev == [] and not p.inventory
        # 同一脚本、有行情 -> 正常送货（对照组）
        room.prices[const.GIFT_EVENTS[0].good] = 100
        engine._roll_gifts(room, ScriptRng([0]), p, ev2 := [])
        assert p.inventory[const.GIFT_EVENTS[0].good].qty == const.GIFT_EVENTS[0].qty
        assert len(ev2) == 1

    def test_health_event_first_hit_breaks(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        engine._roll_health(ScriptRng([1, 0]), p, ev := [])
        assert p.health == 80  # 第二条：闷棍 -20
        assert len(ev) == 1 and "-20" in ev[0]
        assert p.stats.min_health == 80

    def test_hospital_admission(self):
        room, _ = make_room(1, days=20)
        p = room.players["u0"]
        p.health = 70
        engine._check_hospital(room, ScriptRng([1, 500]), p, ev := [])
        assert p.status == ST_HOSPITAL and p.hospital_days == 2
        assert p.debt == 5500 + 2 * 1500
        assert p.health == 80
        assert "强制住院" in ev[0]

    def test_no_admission_when_few_days_left(self):
        room, _ = make_room(1, days=20)
        room.day = 17  # days_left = 3，不强制住院
        p = room.players["u0"]
        p.health = 50
        engine._check_hospital(room, quiet_rng(), p, ev := [])
        assert p.status == ST_ACTIVE and not ev

    def test_money_loss_formula(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.cash = 12345
        engine._roll_money_loss(ScriptRng([1, 1, 0]), p, ev := [])
        assert p.cash == (12345 // 100) * 60  # 40% 那条，先整除百
        assert "4,965" in ev[0]

    def test_money_loss_bank_event(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.bank = 10000
        engine._roll_money_loss(ScriptRng([1, 1, 1, 1, 0]), p, ev := [])
        assert p.bank == 100 * 85  # 15%
        assert len(ev) == 1

    def test_hacker_small_and_mid(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.bank = 500
        engine._roll_hacker(ScriptRng([0]), p, ev := [])
        assert p.bank == 500 and not ev  # 小于 1000 无事
        p.bank = 50000
        engine._roll_hacker(ScriptRng([0, 4]), p, ev2 := [])
        assert p.bank == 50000 + 50000 // 5  # 只赚不赔
        assert "塞了" in ev2[0]

    def test_hacker_big_bank(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.bank = 200000
        engine._roll_hacker(ScriptRng([0, 8, 1]), p, ev := [])  # 1 % 3 != 0 -> 扣
        assert p.bank == 200000 - 20000
        p.bank = 200000
        engine._roll_hacker(ScriptRng([0, 8, 3]), p, ev2 := [])  # 3 % 3 == 0 -> 加
        assert p.bank == 220000

    def test_hacker_off_by_default_like_original(self):
        """原版每局默认不开黑客（OnNewGame 置 FALSE），未配置时不该触发。"""
        script = [1] * 23 + [25, 0]  # 4 白捡+12 健康+7 破财全错过，第 24 次命中黑客判定
        room, _ = make_room(1)
        p = room.players["u0"]
        p.bank = 50000
        engine._process_player_day(room, ScriptRng(list(script)), p)
        assert p.bank == 50500  # 仅日息 1%，黑客未触发

        room2, _ = make_room(1, settings={"enable_hacker": True})
        p2 = room2.players["u0"]
        p2.bank = 50000
        engine._process_player_day(room2, ScriptRng(list(script)), p2)
        assert p2.bank == 50500 + 50500  # 结息后再被黑客塞满 bank//(1+0)

    def test_thug_when_debt_high(self):
        room, r = make_room(1)
        p = room.players["u0"]
        p.debt = 150000
        everyone_stays(room, r)
        assert p.health == 100 - const.THUG_DAMAGE
        assert p.status == ST_ACTIVE  # 当天不死

    def test_death_by_thug_next_day(self):
        room, r = make_room(1)
        p = room.players["u0"]
        p.debt = 150000
        p.health = 25
        room.day = room.days_total - 2  # 剩余天数不足，避免触发强制住院
        everyone_stays(room, r)
        assert p.health == -5 and p.status == ST_ACTIVE
        res = everyone_stays(room, r)
        assert p.status == ST_FINISHED and p.finish_reason == FIN_DEAD
        assert p.final_score is None
        assert res.settlement is not None  # 唯一玩家死亡 -> 直接结算

    def test_interest_applies_during_hospital(self):
        room, r = make_room(2)
        p = room.players["u0"]
        p.status = ST_HOSPITAL
        p.hospital_days = 2
        engine.move(room, r, "u1", 3, 0.0)  # 只需活跃玩家行动即可推进
        assert room.day == 2
        assert p.debt == 6050
        assert p.hospital_days == 1
        engine.move(room, r, "u1", 4, 0.0)
        assert p.status == ST_ACTIVE  # 出院

    def test_single_player_hospital_auto_skips_days(self):
        """单人被强制住院后自动跳天，不会死锁。"""
        room, _ = make_room(1, days=20)
        p = room.players["u0"]
        p.health = 50  # 会触发强制住院（quiet rng：住 2 天）
        r = quiet_rng()
        res = engine.move(room, r, "u0", 3, 0.0)
        # 住院当天 + 自动跳过的天数，直到出院当天玩家重新可行动
        assert p.status == ST_ACTIVE
        assert len(res.day_reports) >= 2
        assert room.day >= 3
        assert not p.moved


class TestSettlement:
    def test_final_day_liquidation(self):
        room, r = make_room(1, days=5)
        p = room.players["u0"]
        while room.day < room.days_total:
            everyone_stays(room, r)
        # 最后一天：持有一批货，其中一种当日无行情
        p.inventory.clear()
        p.inventory[0] = Holding(qty=10, avg_cost=100)
        p.inventory[5] = Holding(qty=3, avg_cost=100)
        room.prices[0] = 300
        room.prices[5] = 0  # 无行情 -> 白扔（原版行为）
        cash_before = p.cash
        res = everyone_stays(room, r)
        assert res.settlement is not None
        assert p.status == ST_FINISHED and p.finish_reason == FIN_NORMAL
        assert p.cash == cash_before + 3000
        assert p.final_score == p.cash + p.bank - p.debt
        entry = res.settlement.entries[0]
        assert entry.score == p.final_score
        assert entry.fame_title == "德艺双馨"

    def test_settlement_ordering_and_dead(self):
        room, r = make_room(3, days=5)
        room.players["u1"].health = -5  # 直接判死（下一次日结算）
        room.players["u0"].cash = 100000
        while room.day < room.days_total:
            everyone_stays(room, r)
        res = everyone_stays(room, r)
        s = res.settlement
        assert s is not None
        assert [e.uid for e in s.entries][0] == "u0"
        dead = [e for e in s.entries if e.reason == FIN_DEAD]
        assert len(dead) == 1 and dead[0].uid == "u1" and dead[0].score is None
        assert s.entries[-1].uid == "u1"  # 死者垫底

    def test_surrender_and_last_man_settles(self):
        room, r = make_room(2, days=10)
        room.players["u0"].inventory[0] = Holding(qty=5, avg_cost=100)
        room.prices[0] = 200
        res1 = engine.surrender(room, r, "u0", 0.0)
        p0 = room.players["u0"]
        assert p0.status == ST_FINISHED and p0.finish_reason == FIN_SURRENDER
        assert p0.final_score == 2000 + 1000 + 0 - 5500
        assert res1.settlement is None  # 还有人在玩
        res2 = engine.surrender(room, r, "u1", 0.0)
        assert res2.settlement is not None
        with pytest.raises(GameError):
            engine.surrender(room, r, "u0", 0.0)  # 已离场

    def test_surrender_of_last_blocker_advances_day(self):
        room, r = make_room(2, days=10)
        engine.move(room, r, "u0", 3, 0.0)
        res = engine.surrender(room, r, "u1", 0.0)
        assert room.day == 2
        assert res.day_reports

    def test_achievements(self):
        room, _ = make_room(1)
        p = room.players["u0"]
        p.final_score = 2_000_000
        p.stats.ever_debt_free = True
        p.stats.trades = 100
        p.stats.min_health = 5
        p.stats.cafe_times = 3
        p.stats.intel_times = 5
        p.stats.best_day_gain = 200000
        p.stats.gift_profit = 60000
        names = engine._achievements(p)
        assert "百万富翁" in names and "千万富豪" not in names
        assert {"无债一身轻", "一夜暴富", "倒爷祖师", "空手套白狼",
                "九死一生", "干净买卖", "网吧常客", "包打听"} <= set(names)

    def test_leaderboard_merge(self):
        s = engine.Settlement(days_total=40)
        s.entries = [
            engine.SettleEntry(uid="a", name="甲", reason=FIN_NORMAL, score=8000,
                               fame_title="有口皆碑", score_title="黑市传奇"),
            engine.SettleEntry(uid="b", name="乙", reason=FIN_NORMAL, score=-100),
            engine.SettleEntry(uid="c", name="丙", reason=FIN_DEAD, score=None),
        ]
        board, changed = engine.merge_leaderboard([], s, "群A", ts=123.0)
        assert changed
        assert board[0]["name"] == const.SEED_CHAMPION["name"]  # 种子榜首
        assert board[1]["name"] == "甲"
        assert s.entries[0].board_rank == 2
        assert s.entries[1].board_rank is None  # 破产不上榜
        assert s.entries[2].board_rank is None  # 死亡不上榜

    def test_leaderboard_truncates_to_ten(self):
        board: list[dict] = []
        for i in range(15):
            s = engine.Settlement(days_total=40)
            s.entries = [
                engine.SettleEntry(uid=f"u{i}", name=f"玩家{i}", reason=FIN_NORMAL,
                                   score=1000 + i, score_title="t", fame_title="f")
            ]
            board, _ = engine.merge_leaderboard(board, s, "群", ts=float(i))
        assert len(board) == const.LEADERBOARD_SIZE
        assert board[0]["score"] == const.SEED_CHAMPION["score"]


class TestSerialization:
    def test_room_roundtrip(self):
        room, r = make_room(2, days=15, settings={"enable_hacker": False})
        engine.buy(room, "u0", 2, 50) if room.prices[2] > 0 else None
        engine.buy_intel(room, r, "u0")
        room.players["u1"].status = ST_HOSPITAL
        room.players["u1"].hospital_days = 2
        everyone_stays(room, r)
        blob = json.dumps(room.to_dict(), ensure_ascii=False)
        restored = Room.from_dict(json.loads(blob))
        assert restored.to_dict() == room.to_dict()
        assert restored.players["u0"].stats.intel_times == 1
        assert restored.setting("enable_hacker", True) is False

    def test_player_defaults_on_missing_fields(self):
        p = Player.from_dict({"uid": "x", "name": "某人"})
        assert p.cash == const.START_CASH and p.status == ST_ACTIVE


class TestFuzz:
    """随机操作轰炸：不变量与可终止性。"""

    def test_random_games_hold_invariants(self):
        for seed in range(40):
            sys_rng = random.Random(seed)
            n = sys_rng.randint(1, 4)
            days = sys_rng.randint(5, 12)
            game_rng = random.Random(seed + 10000)
            room = engine.create_room("f", "u0", "甲", days, {}, 0.0)
            for i in range(1, n):
                engine.join_room(room, f"u{i}", f"玩家{i}")
            engine.start_game(room, game_rng, "u0", 0.0)
            settled = None
            guard = 0
            while settled is None:
                guard += 1
                assert guard < 500, "对局未在合理步数内终止"
                actor = None
                for p in room.active_players():
                    if not p.moved:
                        actor = p
                        break
                assert actor is not None or not room.in_game_players()
                if actor is None:
                    break
                for _ in range(sys_rng.randint(0, 4)):
                    self._random_op(room, game_rng, sys_rng, actor.uid)
                    self._check_invariants(room)
                if sys_rng.random() < 0.02:
                    res = engine.surrender(room, game_rng, actor.uid, 0.0)
                else:
                    dest = sys_rng.choice([None] + list(range(10)))
                    try:
                        res = engine.move(room, game_rng, actor.uid, dest, 0.0)
                    except GameError:  # 原地"去"
                        res = engine.move(room, game_rng, actor.uid, None, 0.0)
                self._check_invariants(room)
                settled = res.settlement
                assert room.day <= room.days_total + 1
            assert settled is not None
            assert len(settled.entries) == n

    @staticmethod
    def _random_op(room, game_rng, sys_rng, uid):
        ops = [
            lambda: engine.buy(room, uid, sys_rng.randrange(8), sys_rng.choice([None, 1, 5, 500])),
            lambda: engine.sell(room, uid, sys_rng.randrange(8), sys_rng.choice([None, 1, 3])),
            lambda: engine.deposit(room, uid, sys_rng.choice([None, 100, 10**7])),
            lambda: engine.withdraw(room, uid, sys_rng.choice([None, 50])),
            lambda: engine.repay(room, uid, sys_rng.choice([None, 100])),
            lambda: engine.heal(room, uid, sys_rng.choice([None, 1, 200])),
            lambda: engine.upgrade_house(room, uid),
            lambda: engine.cyber_cafe(room, game_rng, uid),
            lambda: engine.buy_intel(room, game_rng, uid),
        ]
        try:
            sys_rng.choice(ops)()
        except GameError:
            pass

    @staticmethod
    def _check_invariants(room):
        for p in room.players.values():
            assert p.cash >= 0, "现金为负"
            assert p.bank >= 0, "存款为负"
            assert p.debt >= 0, "债务为负"
            assert p.health <= const.MAX_HEALTH
            assert 0 <= p.fame <= const.START_FAME
            assert p.used_capacity() <= p.capacity <= const.MAX_CAPACITY
            for h in p.inventory.values():
                assert h.qty > 0 and h.avg_cost >= 0
        for price in room.prices:
            assert price >= 0
