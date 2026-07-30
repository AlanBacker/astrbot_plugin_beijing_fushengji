"""行情模块测试：价格生成、下架、价格事件、情报、市场冲击。"""

from __future__ import annotations

import random

from conftest import ScriptRng, make_room

from core import const, engine, market


def _bare_room(days_total=20, day=1):
    room = engine.create_room("t:m", "u0", "甲", days_total, {}, 0.0)
    room.phase = "running"
    room.day = day
    return room


class TestGeneratePrices:
    def test_price_bounds_over_many_seeds(self):
        room = _bare_room()
        for seed in range(300):
            market.generate_prices(room, random.Random(seed))
            for i, g in enumerate(const.GOODS):
                p = room.prices[i]
                assert p == 0 or g.base <= p <= g.base + g.rand_range - 1

    def test_delist_at_most_three(self):
        room = _bare_room()
        for seed in range(300):
            market.generate_prices(room, random.Random(seed))
            assert sum(1 for p in room.prices if p == 0) <= const.DELIST_ROLLS

    def test_full_market_on_last_two_days(self):
        room = _bare_room(days_total=20, day=19)  # days_left = 1
        for seed in range(100):
            market.generate_prices(room, random.Random(seed))
            assert all(p > 0 for p in room.prices)
        room.day = 20  # days_left = 0（最后一天）
        for seed in range(100):
            market.generate_prices(room, random.Random(seed))
            assert all(p > 0 for p in room.prices)
        room.day = 18  # days_left = 2，仍会下架
        seen_zero = False
        for seed in range(200):
            market.generate_prices(room, random.Random(seed))
            seen_zero = seen_zero or any(p == 0 for p in room.prices)
        assert seen_zero

    def test_impact_flow_reset(self):
        room = _bare_room()
        room.impact_flow[0] = 99999
        market.generate_prices(room, random.Random(1))
        assert room.impact_flow == [0] * const.N_GOODS


class TestPriceEvents:
    def test_multiply_event(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        # 第 0 条（freq 170，进口玩具 x2）命中，其余错过
        rng = ScriptRng([0] + [1] * 13)
        headlines = market.roll_price_events(room, rng)
        assert room.prices[5] == 200
        assert headlines == [const.PRICE_EVENTS[0].text]

    def test_divide_event_can_zero_price(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        room.prices[2] = 5  # 盗版VCD 5 // 8 = 0，商品当日消失（原版行为）
        rng = ScriptRng([1] * 13 + [0])  # 只命中最后一条（VCD ÷8）
        market.roll_price_events(room, rng)
        assert room.prices[2] == 0

    def test_event_skipped_when_delisted(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        room.prices[5] = 0
        rng = ScriptRng([0] + [1] * 13)  # 命中玩具 x2，但玩具未上市
        headlines = market.roll_price_events(room, rng)
        assert room.prices[5] == 0 and headlines == []

    def test_same_good_can_be_hit_twice(self):
        """古董有两条事件（x5 与 x8），同日齐发 = x40（原版无去重）。"""
        room = _bare_room()
        room.prices = [1000] * const.N_GOODS
        vals = [1] * 14
        vals[2] = 0  # 古董 x5
        vals[6] = 0  # 古董 x8
        market.roll_price_events(room, ScriptRng(vals))
        assert room.prices[4] == 1000 * 5 * 8


class TestTip:
    def test_truthful_tip_forces_event(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        room.tip = market.make_tip(room, ScriptRng([0, 6]), accuracy_pct=75)
        assert room.tip.truthful and room.tip.event_idx == 6  # 古董 x8
        headlines = market.roll_price_events(room, ScriptRng([1] * 14))  # 自然全错过
        assert room.prices[4] == 800
        assert const.PRICE_EVENTS[6].text in headlines
        assert room.tip is None  # 情报只管一天

    def test_fake_tip_forces_nothing(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        # accuracy 0 -> 必为假消息
        room.tip = market.make_tip(room, ScriptRng([99, 3, 0]), accuracy_pct=0)
        assert not room.tip.truthful
        market.roll_price_events(room, ScriptRng([1] * 14))
        assert room.prices == [100] * const.N_GOODS

    def test_truthful_tip_no_double_apply_when_naturally_hit(self):
        room = _bare_room()
        room.prices = [100] * const.N_GOODS
        room.tip = market.make_tip(room, ScriptRng([0, 6]), accuracy_pct=75)
        vals = [1] * 14
        vals[6] = 0  # 自然命中同一条
        headlines = market.roll_price_events(room, ScriptRng(vals))
        assert room.prices[4] == 800  # 只乘一次
        assert headlines.count(const.PRICE_EVENTS[6].text) == 1


class TestImpact:
    def test_steps_and_caps(self):
        room = _bare_room()
        room.prices = [1000] * const.N_GOODS
        assert market.effective_price(room, 0) == 1000
        room.impact_flow[0] = const.IMPACT_UNIT  # +1 档
        assert market.impact_steps(room, 0) == 1
        assert market.effective_price(room, 0) == 1050
        room.impact_flow[0] = const.IMPACT_UNIT * 100  # 远超封顶
        assert market.impact_steps(room, 0) == const.IMPACT_MAX_STEPS
        assert market.effective_price(room, 0) == 1300
        room.impact_flow[0] = -const.IMPACT_UNIT * 100
        assert market.effective_price(room, 0) == 700

    def test_effective_price_floor_is_one(self):
        room = _bare_room()
        room.prices[2] = 1
        room.impact_flow[2] = -const.IMPACT_UNIT * 100
        assert market.effective_price(room, 2) == 1

    def test_disabled_returns_base(self):
        room = _bare_room()
        room.prices[0] = 1000
        room.impact_flow[0] = const.IMPACT_UNIT * 3
        assert market.effective_price(room, 0, enabled=False) == 1000

    def test_record_flow_signs(self):
        room = _bare_room()
        room.prices[0] = 200
        market.record_flow(room, 0, 10, is_buy=True)
        assert room.impact_flow[0] == 2000
        market.record_flow(room, 0, 5, is_buy=False)
        assert room.impact_flow[0] == 1000

    def test_negative_flow_rounds_toward_lower_step(self):
        """整除语义：-1 元流量即进入 -1 档（floor 除法），验证行为符合定义。"""
        room = _bare_room()
        room.prices = [1000] * const.N_GOODS
        room.impact_flow[0] = -1
        assert market.impact_steps(room, 0) == -1


class TestBuyAllWithImpact:
    def test_buy_all_uses_effective_price(self):
        room, _ = make_room(1, days=20, settings={"market_impact": True})
        room.prices = [0] * const.N_GOODS
        room.prices[0] = 100
        room.impact_flow[0] = const.IMPACT_UNIT * 2  # +10% -> 110 元
        p = room.players["u0"]
        p.cash = 1100
        res = engine.buy(room, "u0", 0, None)
        assert p.inventory[0].qty == 10  # 1100 // 110
        assert p.cash == 0
        assert "买入" in res.lines[0]
