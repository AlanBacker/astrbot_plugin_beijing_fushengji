"""每日行情：价格生成、价格事件应用与市场冲击。

价格生成与下架规则同原版：
    price = base + rand(range)
    平日有放回抽 3 次 rand(8)，抽中的商品当日下架（价格置 0）；
    最后 2 个交易日不下架（清仓期，8 种全上）。
价格事件把当日价 xN 或 //N，//N 可能除成 0 使商品当日消失（原版行为）。

市场冲击（创新，可配置关闭）：
    同一商品当日全房间净买入金额每满 IMPACT_UNIT，实际成交价 +5%；
    净卖出同理 -5%；封顶 ±30%。冲击以当日基准价计流量、逐笔生效，
    对全体玩家一致。强制平仓与认输清算不计冲击。
"""

from __future__ import annotations

import random

from . import const
from .models import Room, Tip


def generate_prices(room: Room, rng: random.Random) -> None:
    """为新的一天生成基准价并重置冲击流量。"""
    prices = [g.base + rng.randrange(g.rand_range) for g in const.GOODS]
    if not room.is_full_market_day():
        for _ in range(const.DELIST_ROLLS):
            prices[rng.randrange(const.N_GOODS)] = 0
    room.prices = prices
    room.impact_flow = [0] * const.N_GOODS
    room.price_marks = [0] * const.N_GOODS


def roll_price_events(room: Room, rng: random.Random) -> list[str]:
    """逐条判定 14 条价格事件（互不 break），返回新闻列表。

    若房间存在指向今日的真情报（room.tip），保证对应事件必然生效：
    自然命中则顺其自然，否则补触发一次。
    """
    headlines: list[str] = []
    hit: set[int] = set()
    for idx, ev in enumerate(const.PRICE_EVENTS):
        if rng.randrange(const.BUSINESS_POOL) % ev.freq == 0:
            if _apply_price_event(room, idx):
                hit.add(idx)
                headlines.append(ev.text)
                room.boom_total += const.boom_points(ev)

    tip = room.tip
    if tip and tip.truthful and tip.event_idx >= 0 and tip.event_idx not in hit:
        if _apply_price_event(room, tip.event_idx):
            headlines.append(const.PRICE_EVENTS[tip.event_idx].text)
            room.boom_total += const.boom_points(const.PRICE_EVENTS[tip.event_idx])
    room.tip = None  # 情报只管一天，无论真假均失效
    return headlines


def _apply_price_event(room: Room, idx: int) -> bool:
    """把价格事件应用到当日价；商品未上市则跳过（原版行为）。"""
    ev = const.PRICE_EVENTS[idx]
    price = room.prices[ev.good]
    if price <= 0:
        return False
    room.prices[ev.good] = price * ev.mul if ev.mul else price // ev.div
    room.price_marks[ev.good] = 1 if ev.mul else -1
    return True


def make_tip(room: Room, rng: random.Random, accuracy_pct: int) -> Tip:
    """生成一条明日情报。

    真消息（accuracy_pct%）：随机挑一条价格事件，明日保证生效；
    假消息：随机编一个商品与涨跌方向，明日听天由命。
    """
    if rng.randrange(100) < accuracy_pct:
        idx = rng.randrange(len(const.PRICE_EVENTS))
        ev = const.PRICE_EVENTS[idx]
        return Tip(good=ev.good, is_up=ev.is_up, truthful=True, event_idx=idx)
    return Tip(
        good=rng.randrange(const.N_GOODS),
        is_up=rng.randrange(2) == 0,
        truthful=False,
        event_idx=-1,
    )


# ---------------------------------------------------------------------------
# 市场冲击
# ---------------------------------------------------------------------------


def impact_steps(room: Room, good: int) -> int:
    """当前冲击档位，负数表示价格被砸低。"""
    steps = room.impact_flow[good] // const.IMPACT_UNIT
    return max(-const.IMPACT_MAX_STEPS, min(const.IMPACT_MAX_STEPS, steps))


def effective_price(room: Room, good: int, enabled: bool = True) -> int:
    """含市场冲击的实际成交价；未上市返回 0。"""
    base = room.prices[good]
    if base <= 0:
        return 0
    if not enabled:
        return base
    pct = 100 + const.IMPACT_STEP_PCT * impact_steps(room, good)
    return max(1, base * pct // 100)


def record_flow(room: Room, good: int, qty: int, is_buy: bool) -> None:
    """按当日基准价记录净买卖流量（买正卖负）。"""
    value = qty * room.prices[good]
    room.impact_flow[good] += value if is_buy else -value
