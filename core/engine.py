"""游戏规则引擎（纯逻辑，无 IO、无 AstrBot 依赖）。

对外暴露的每个操作函数都遵循同一约定：
    - 参数：room（就地修改）、rng、玩家 uid 及操作参数
    - 非法操作抛 errors.GameError（面向玩家的中文提示）
    - 返回 ActionResult；若该操作恰好凑齐了所有人的行动，则同时携带
      day_report（新的一天）或 settlement（对局结束）

每日流程严格保持原版顺序：
    生成价格 -> 价格事件(房间级) -> 逐玩家[利息 -> 白捡商品 -> 健康事件
    -> 强制住院 -> 虚弱警告 -> 死亡判定 -> 亏钱事件 -> 黑客 -> 现金清负
    -> 讨债打手] -> 天数推进/结算
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from . import const, market
from .errors import GameError
from .models import (
    FIN_DEAD,
    FIN_NORMAL,
    FIN_SURRENDER,
    PHASE_RUNNING,
    PHASE_SIGNUP,
    ST_ACTIVE,
    ST_FINISHED,
    ST_HOSPITAL,
    Holding,
    Player,
    Room,
)

ALL = None  # 数量/金额参数里的"全部"哨兵


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class DayReport:
    """新的一天开始时的日报数据。"""

    day: int
    days_total: int
    headlines: list[str] = field(default_factory=list)
    player_events: list[tuple[str, list[str]]] = field(default_factory=list)
    standings: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SettleEntry:
    uid: str
    name: str
    reason: str  # FIN_*
    score: int | None  # 死亡为 None
    fame: int = 0
    fame_title: str = ""
    score_title: str = ""  # 上榜称号（正分才有）
    achievements: list[str] = field(default_factory=list)
    board_rank: int | None = None  # 进入历史榜的名次（main 合榜后回填）


@dataclass
class Settlement:
    days_total: int
    entries: list[SettleEntry] = field(default_factory=list)


@dataclass
class ActionResult:
    lines: list[str] = field(default_factory=list)
    # 一次操作可能推进多天（如全员住院时自动跳天），故为列表；通常至多 1 个
    day_reports: list[DayReport] = field(default_factory=list)
    settlement: Settlement | None = None


# ---------------------------------------------------------------------------
# 房间生命周期
# ---------------------------------------------------------------------------


def create_room(
    room_id: str, uid: str, name: str, days: int, settings: dict[str, Any], now_ts: float
) -> Room:
    if not const.MIN_DAYS <= days <= const.MAX_DAYS:
        raise GameError(f"天数须在 {const.MIN_DAYS}~{const.MAX_DAYS} 之间。")
    room = Room(room_id=room_id, creator=uid, days_total=days, settings=dict(settings))
    room.day_started_at = now_ts
    _join(room, uid, name)
    return room


def join_room(room: Room, uid: str, name: str) -> ActionResult:
    if room.phase != PHASE_SIGNUP:
        raise GameError("本局已经开始，等这局结束再来吧。")
    if uid in room.players:
        raise GameError("你已经在候车名单里了。")
    max_players = int(room.setting("max_players", const.MAX_PLAYERS))
    if len(room.players) >= max_players:
        raise GameError(f"人满为患（{max_players} 人上限），下局请早。")
    _join(room, uid, name)
    n = len(room.players)
    return ActionResult(
        lines=[
            f"🚆 {name} 挤上了进京的绿皮火车（{n}/{max_players} 人）。",
            "房主发送「开始」发车；其他人可发送「加入」上车。",
        ]
    )


def _join(room: Room, uid: str, name: str) -> None:
    room.players[uid] = Player(uid=uid, name=name)


def start_game(room: Room, rng: random.Random, uid: str, now_ts: float) -> ActionResult:
    if room.phase != PHASE_SIGNUP:
        raise GameError("本局已经开始了。")
    if uid != room.creator:
        raise GameError("只有房主能发车。")
    room.phase = PHASE_RUNNING
    room.day = 1
    room.day_started_at = now_ts
    # 开局立即结息一次（原版行为：5000 -> 5500）
    for p in room.players.values():
        p.debt += p.debt * const.DEBT_RATE_PCT // 100
    market.generate_prices(room, rng)
    for p in room.players.values():
        p.net_worth_yesterday = p.net_worth(room.prices)
    return ActionResult(
        lines=[f"🚩 第 1 天，{const.LOCATIONS[const.START_LOCATION]}。列车到站，各凭本事。"]
    )


def dissolve(room: Room, uid: str, is_admin: bool) -> None:
    if uid != room.creator and not is_admin:
        raise GameError("只有房主或管理员能解散本局。")


# ---------------------------------------------------------------------------
# 通用校验
# ---------------------------------------------------------------------------


def _require_running(room: Room) -> None:
    if room.phase != PHASE_RUNNING:
        raise GameError("本局还没开始。", "房主发送「浮生记 开始」发车。")


def _get_player(room: Room, uid: str) -> Player:
    p = room.players.get(uid)
    if p is None:
        raise GameError("你不在本局中。", "等这局结束后发送「浮生记 创建」再来。")
    return p


def _require_can_act(room: Room, uid: str) -> Player:
    """要求玩家处于可自由操作状态（存活、未动身）。"""
    _require_running(room)
    p = _get_player(room, uid)
    if p.status == ST_FINISHED:
        raise GameError("你这局已经离场，坐等大伙儿结算吧。")
    if p.status == ST_HOSPITAL:
        raise GameError(f"你还在医院躺着（剩 {p.hospital_days} 天），动弹不得。")
    if p.moved:
        raise GameError("你已经动身赶路了，今天不能再操作。", "等其他人行动完就是新的一天。")
    return p


def _fmt(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# 交易与设施
# ---------------------------------------------------------------------------


def buy(room: Room, uid: str, good: int, qty: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    g = const.GOODS[good]
    impact_on = bool(room.setting("market_impact", True))
    price = market.effective_price(room, good, impact_on)
    if price <= 0:
        raise GameError(f"今天市面上没人卖{g.name}。")
    cap_left = p.capacity_left()
    if qty is ALL:
        qty = min(p.cash // price, cap_left)
        if qty <= 0:
            raise GameError("要么钱不够，要么屋里塞不下，一件也买不了。")
    if qty <= 0:
        raise GameError("数量得是正数。")
    if qty > cap_left:
        raise GameError(f"屋里只塞得下 {cap_left} 件了。", "发送「浮生记 租房」扩容。")
    cost = qty * price
    if cost > p.cash:
        raise GameError(f"现金不够：需要 {_fmt(cost)} 元，你只有 {_fmt(p.cash)} 元。")

    p.cash -= cost
    h = p.inventory.setdefault(good, Holding())
    h.avg_cost = (h.avg_cost * h.qty + cost) // (h.qty + qty)
    h.qty += qty
    p.stats.trades += 1
    if impact_on:
        market.record_flow(room, good, qty, is_buy=True)

    lines = [
        f"🛒 买入 {g.name} ×{qty}（单价 {_fmt(price)}），花费 {_fmt(cost)} 元。",
        f"现金 {_fmt(p.cash)} 元｜仓库 {p.used_capacity()}/{p.capacity}",
    ]
    if impact_on and market.impact_steps(room, good) > 0:
        lines.append(f"📈 你这一通扫货，{g.short}的行情被抬高了！")
    return ActionResult(lines=lines)


def sell(room: Room, uid: str, good: int, qty: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    g = const.GOODS[good]
    h = p.inventory.get(good)
    if not h or h.qty <= 0:
        raise GameError(f"你手里没有{g.name}。")
    impact_on = bool(room.setting("market_impact", True))
    price = market.effective_price(room, good, impact_on)
    if price <= 0:
        raise GameError(f"{g.name}今天没有行情，出不了手。")
    if qty is ALL:
        qty = h.qty
    if qty <= 0:
        raise GameError("数量得是正数。")
    if qty > h.qty:
        raise GameError(f"你只有 {h.qty} 件{g.name}。")

    proceeds = qty * price
    profit = (price - h.avg_cost) * qty
    p.cash += proceeds
    h.qty -= qty
    if h.qty == 0:
        del p.inventory[good]
    p.stats.trades += 1
    if h.avg_cost == 0 and profit > 0:
        p.stats.gift_profit += profit
    if impact_on:
        market.record_flow(room, good, qty, is_buy=False)

    lines = [
        f"💰 卖出 {g.name} ×{qty}（单价 {_fmt(price)}），进账 {_fmt(proceeds)} 元。",
        f"本笔{'盈利' if profit >= 0 else '亏损'} {_fmt(abs(profit))} 元｜现金 {_fmt(p.cash)} 元",
    ]
    if g.fame_cost > 0:
        p.fame = max(0, p.fame - g.fame_cost)
        p.stats.sold_shady = True
        lines.append(f"😈 卖{g.short}是缺德买卖，名声 -{g.fame_cost}（现 {p.fame}）。")
    if impact_on and market.impact_steps(room, good) < 0:
        lines.append(f"📉 你这一通抛售，{g.short}的价格被砸下去了！")
    return ActionResult(lines=lines)


def deposit(room: Room, uid: str, amount: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    if amount is ALL:
        amount = p.cash
    if amount <= 0:
        raise GameError("金额得是正数。" if p.cash > 0 else "你身上一分钱都没有。")
    if amount > p.cash:
        raise GameError(f"现金只有 {_fmt(p.cash)} 元。")
    p.cash -= amount
    p.bank += amount
    return ActionResult(
        lines=[f"🏦 存入 {_fmt(amount)} 元（日息 1%）。存款 {_fmt(p.bank)}｜现金 {_fmt(p.cash)}"]
    )


def withdraw(room: Room, uid: str, amount: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    if amount is ALL:
        amount = p.bank
    if amount <= 0:
        raise GameError("金额得是正数。" if p.bank > 0 else "你在银行没有存款。")
    if amount > p.bank:
        raise GameError(f"存款只有 {_fmt(p.bank)} 元。")
    p.bank -= amount
    p.cash += amount
    return ActionResult(
        lines=[f"🏦 取出 {_fmt(amount)} 元。存款 {_fmt(p.bank)}｜现金 {_fmt(p.cash)}"]
    )


def repay(room: Room, uid: str, amount: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    if p.debt <= 0:
        raise GameError("你已经无债一身轻了。")
    if amount is ALL:
        amount = min(p.cash, p.debt)
    if amount <= 0:
        raise GameError("金额得是正数。" if p.cash > 0 else "你身上一分钱都没有。")
    if amount > p.cash:
        raise GameError(f"现金只有 {_fmt(p.cash)} 元。")
    if amount > p.debt:
        amount = p.debt
    p.cash -= amount
    p.debt -= amount
    lines = [f"📮 邮局汇款还债 {_fmt(amount)} 元。剩余债务 {_fmt(p.debt)}｜现金 {_fmt(p.cash)}"]
    if p.debt == 0:
        p.stats.ever_debt_free = True
        assets = p.net_worth(room.prices)
        for line_limit, text in const.DEBT_CLEAR_TIERS:
            if assets < line_limit:
                lines.append(text)
                break
    return ActionResult(lines=lines)


def heal(room: Room, uid: str, points: int | None) -> ActionResult:
    p = _require_can_act(room, uid)
    lack = const.MAX_HEALTH - p.health
    if lack <= 0:
        raise GameError("你壮得像头牛，医生请你回去。")
    if points is ALL:
        points = min(lack, p.cash // const.HOSPITAL_PRICE_PER_POINT)
        if points <= 0:
            raise GameError(
                f"看病 {_fmt(const.HOSPITAL_PRICE_PER_POINT)} 元/点，你的现金一点也治不起。"
            )
    if points <= 0:
        raise GameError("点数得是正数。")
    if points > lack:
        raise GameError(f"最多还能治 {lack} 点。")
    cost = points * const.HOSPITAL_PRICE_PER_POINT
    if cost > p.cash:
        raise GameError(f"治 {points} 点要 {_fmt(cost)} 元现金，你只有 {_fmt(p.cash)} 元。")
    p.cash -= cost
    p.health += points
    return ActionResult(
        lines=[f"🏥 花 {_fmt(cost)} 元治了 {points} 点。健康 {p.health}/100｜现金 {_fmt(p.cash)}"]
    )


def upgrade_house(room: Room, uid: str) -> ActionResult:
    p = _require_can_act(room, uid)
    if p.capacity >= const.MAX_CAPACITY:
        raise GameError(f"你已经住进了 {const.MAX_CAPACITY} 容量的大房子，中介都没得赚了。")
    if p.cash < const.HOUSE_MIN_CASH:
        raise GameError(
            f"中介上下打量你一眼：'现金不到 {_fmt(const.HOUSE_MIN_CASH)} 的免谈。'"
        )
    # 原版设定：中介实扣与报价不符（黑中介）
    if p.cash <= const.HOUSE_MIN_CASH:
        quote = const.HOUSE_AGENT_QUOTE_FLAT
        real = const.HOUSE_FLAT_COST
        p.cash -= real
    else:
        quote = const.HOUSE_AGENT_QUOTE_HALF
        new_cash = p.cash // 2 - 2000
        real = p.cash - new_cash
        p.cash = new_cash
    p.capacity += const.HOUSE_STEP
    return ActionResult(
        lines=[quote, const.HOUSE_AGENT_DONE.format(cap=p.capacity, real=_fmt(real))]
    )


def cyber_cafe(room: Room, rng: random.Random, uid: str) -> ActionResult:
    p = _require_can_act(room, uid)
    if p.stats.cafe_times >= const.CAFE_MAX_TIMES:
        raise GameError("网管把你轰了出来：'一天到晚蹭活儿，本店恕不接待！'（每局限 3 次）")
    if p.cash < const.CAFE_MIN_CASH:
        raise GameError(f"网吧最低消费 {const.CAFE_MIN_CASH} 元，你这现金进不去门。")
    reward = const.CAFE_REWARD[0] + rng.randrange(const.CAFE_REWARD[1])
    p.cash += reward
    p.stats.cafe_times += 1
    text = const.CAFE_TEXTS[rng.randrange(len(const.CAFE_TEXTS))]
    return ActionResult(
        lines=[
            text.format(amount=reward),
            f"现金 {_fmt(p.cash)} 元（网吧打工 {p.stats.cafe_times}/{const.CAFE_MAX_TIMES} 次）",
        ]
    )


def buy_intel(room: Room, rng: random.Random, uid: str) -> ActionResult:
    p = _require_can_act(room, uid)
    if room.days_left() <= 0:
        raise GameError(const.INTEL_NO_TOMORROW)
    if p.intel_day == room.day:
        raise GameError(const.INTEL_ALREADY)
    price = int(room.setting("intel_price", const.INTEL_DEFAULT_PRICE))
    if p.cash < price:
        raise GameError(f"消息贩子开价 {_fmt(price)} 元，你的现金不够。")
    p.cash -= price
    p.intel_day = room.day
    p.stats.intel_times += 1
    if room.tip is None:
        accuracy = int(room.setting("intel_accuracy", const.INTEL_DEFAULT_ACCURACY))
        room.tip = market.make_tip(room, rng, accuracy)
    tip = room.tip
    g = const.GOODS[tip.good]
    detail = const.INTEL_UP_TEXT if tip.is_up else const.INTEL_DOWN_TEXT
    return ActionResult(
        lines=[
            f"🕶️ 你塞给消息贩子 {_fmt(price)} 元。",
            const.INTEL_TEXTS_INTRO + detail.format(good=g.name),
            "（小道消息仅供参考，信不信由你）",
        ]
    )


# ---------------------------------------------------------------------------
# 移动与回合推进
# ---------------------------------------------------------------------------


def move(room: Room, rng: random.Random, uid: str, dest: int | None, now_ts: float) -> ActionResult:
    """去某地（dest=None 表示留守原地）。凑齐所有人后推进一天。"""
    p = _require_can_act(room, uid)
    if dest is not None:
        if not 0 <= dest < len(const.LOCATIONS):
            raise GameError("没有这个地方。", "发送「浮生记 面板」查看地点编号。")
        if dest == p.location:
            raise GameError("你就在这儿站着呢。", "原地休整请发送「浮生记 留守」。")
    p.moved = True
    p.pending_dest = -1 if dest is None else dest
    where = "原地休整" if dest is None else f"动身前往{const.LOCATIONS[dest]}"
    result = ActionResult(lines=[f"🧳 {p.name} {where}。"])
    _maybe_advance(room, rng, now_ts, result)
    if not result.day_reports and result.settlement is None:
        waiting = room.waiting_players()
        names = "、".join(w.name for w in waiting)
        result.lines.append(f"还差 {len(waiting)} 人行动：{names}")
    return result


def skip_idlers(
    room: Room, rng: random.Random, uid: str, is_admin: bool, now_ts: float, auto: bool = False
) -> ActionResult:
    """把未行动玩家按"留守"处理并推进一天。auto=True 为闲置超时自动触发。"""
    _require_running(room)
    if not auto and uid != room.creator and not is_admin:
        raise GameError("只有房主或管理员能催场。")
    if not auto:
        caller = room.players.get(uid)
        if caller is not None and caller.status == ST_ACTIVE and not caller.moved:
            raise GameError(
                "你自己今天还没行动呢。",
                "先「浮生记 去 <地点>」或「浮生记 留守」，再催别人。",
            )
    waiting = room.waiting_players()
    if not waiting:
        raise GameError("没有需要跳过的玩家。")
    names = "、".join(w.name for w in waiting)
    for w in waiting:
        w.moved = True
        w.pending_dest = -1
    reason = "⏰ 天黑了还没动静" if auto else "⏭️ 房主催场"
    result = ActionResult(lines=[f"{reason}，{names} 被按「留守」处理。"])
    _maybe_advance(room, rng, now_ts, result)
    return result


def surrender(room: Room, rng: random.Random, uid: str, now_ts: float) -> ActionResult:
    _require_running(room)
    p = _get_player(room, uid)
    if p.status == ST_FINISHED:
        raise GameError("你这局已经离场了。")
    _liquidate(p, room.prices)
    p.status = ST_FINISHED
    p.finish_reason = FIN_SURRENDER
    p.final_score = p.cash + p.bank - p.debt
    result = ActionResult(
        lines=[
            const.ENDING_SURRENDER_TEXT,
            f"{p.name} 提前离场，清算净资产 {_fmt(p.final_score)} 元。",
        ]
    )
    if not room.in_game_players():
        result.settlement = _settle(room, rng)
    else:
        _maybe_advance(room, rng, now_ts, result)
    return result


def _maybe_advance(room: Room, rng: random.Random, now_ts: float, result: ActionResult) -> None:
    """若所有可行动玩家均已行动，推进一天并把结果写入 result。

    若推进后在局玩家全都躺在医院（无人能发指令），自动继续推进
    （等价于原版"住院直接扣天数"），直到有人出院或对局结束。
    """
    if room.phase != PHASE_RUNNING or room.waiting_players():
        return
    if not room.in_game_players():
        result.settlement = _settle(room, rng)
        return
    _advance_day(room, rng, now_ts, result)
    while (
        result.settlement is None
        and room.in_game_players()
        and not room.active_players()
    ):
        _advance_day(room, rng, now_ts, result)


def _advance_day(room: Room, rng: random.Random, now_ts: float, result: ActionResult) -> None:
    # 1. 执行已锁定的移动
    for p in room.active_players():
        if p.pending_dest >= 0:
            p.location = p.pending_dest
        p.moved = False
        p.pending_dest = -1

    # 2. 天数推进；玩过最后一天则强制平仓结算（不再生成价格/结息）
    room.day += 1
    if room.day > room.days_total:
        for p in room.in_game_players():
            _liquidate(p, room.prices)
            p.status = ST_FINISHED
            p.finish_reason = FIN_NORMAL
            p.final_score = p.cash + p.bank - p.debt
        result.settlement = _settle(room, rng)
        return

    # 3. 新一天：行情与房间级价格事件
    market.generate_prices(room, rng)
    headlines = market.roll_price_events(room, rng)
    room.headlines = headlines

    report = DayReport(day=room.day, days_total=room.days_total, headlines=headlines)

    # 4. 逐玩家结算（加入顺序）
    for p in room.players.values():
        if p.status == ST_FINISHED:
            continue
        events = _process_player_day(room, rng, p)
        nw = p.net_worth(room.prices)
        p.stats.best_day_gain = max(p.stats.best_day_gain, nw - p.net_worth_yesterday)
        p.net_worth_yesterday = nw
        if events:
            report.player_events.append((p.name, events))

    # 5. 全员离场则直接结算
    if not room.in_game_players():
        result.settlement = _settle(room, rng)
        return

    room.day_started_at = now_ts
    report.standings = standings(room)
    if room.day == room.days_total:
        report.notes.append("今天是最后一天！收摊前记得把货全卖掉，明早强制平仓。")
    elif room.days_left() == 1:
        report.notes.append("倒数第二天，该考虑清仓了。")
    if room.is_full_market_day():
        report.notes.append("清仓季：今天全部 8 种商品都有行情。")
    result.day_reports.append(report)


def _process_player_day(room: Room, rng: random.Random, p: Player) -> list[str]:
    """单个玩家的一天（利息与各类事件），返回事件文本。"""
    events: list[str] = []

    # 利息（住院也照算：债主可不管你躺不躺着）
    p.debt += p.debt * const.DEBT_RATE_PCT // 100
    p.bank += p.bank * const.BANK_RATE_PCT // 100

    if p.status == ST_HOSPITAL:
        p.hospital_days -= 1
        if p.hospital_days <= 0:
            p.status = ST_ACTIVE
            events.append(const.HOSPITAL_DISCHARGE_TEXT)
        else:
            events.append(f"（住院中，还剩 {p.hospital_days} 天）")
        return events

    _roll_gifts(room, rng, p, events)
    _roll_health(rng, p, events)
    _check_hospital(room, rng, p, events)
    if 0 < p.health < const.HEALTH_WARN_LINE:
        events.append(const.HEALTH_WARN_TEXT)
    if p.health < 0:
        p.status = ST_FINISHED
        p.finish_reason = FIN_DEAD
        p.final_score = None
        events.append(const.DEATH_TEXT)
        return events
    _roll_money_loss(rng, p, events)
    if room.setting("enable_hacker", True):
        _roll_hacker(rng, p, events)
    p.cash = max(0, p.cash)
    if p.debt > const.THUG_DEBT_LINE:
        p.health -= const.THUG_DAMAGE
        p.stats.min_health = min(p.stats.min_health, p.health)
        events.append(const.THUG_TEXT)
    return events


def _roll_gifts(room: Room, rng: random.Random, p: Player, events: list[str]) -> None:
    """白捡商品事件：逐条独立判定；房满则中断剩余判定（原版行为）。"""
    for ev in const.GIFT_EVENTS:
        if rng.randrange(const.BUSINESS_POOL) % ev.freq != 0:
            continue
        cap_left = p.capacity_left()
        if cap_left <= 0:
            if ev.add_debt:  # 村长的货：房满货不给、债照加
                p.debt += ev.add_debt
                events.append(ev.text.format(qty=0) + "（屋满没收下货，债却记上了！）")
            else:
                events.append(const.GIFT_HOUSE_FULL_TEXT)
            break
        qty = min(ev.qty, cap_left)
        h = p.inventory.setdefault(ev.good, Holding())
        h.avg_cost = h.avg_cost * h.qty // (h.qty + qty)  # 白捡的按 0 成本摊薄
        h.qty += qty
        if ev.add_debt:
            p.debt += ev.add_debt
        line = ev.text.format(qty=qty)
        if qty < ev.qty:
            line += f"（屋里只塞得下 {qty} 件）"
        events.append(line)


def _roll_health(rng: random.Random, p: Player, events: list[str]) -> None:
    """健康事件：按序判定，命中一条即止。"""
    for ev in const.HEALTH_EVENTS:
        if rng.randrange(const.MISC_POOL) % ev.freq == 0:
            p.health -= ev.damage
            p.stats.min_health = min(p.stats.min_health, p.health)
            events.append(f"{ev.text}（健康 -{ev.damage}，现 {max(p.health, 0)}）")
            return


def _check_hospital(room: Room, rng: random.Random, p: Player, events: list[str]) -> None:
    """健康过低且剩余天数充足 -> 强制住院。"""
    if p.health >= const.HOSPITAL_FORCE_HEALTH:
        return
    if room.days_left() <= const.HOSPITAL_FORCE_MIN_DAYS_LEFT:
        return
    days = 1 + rng.randrange(const.HOSPITAL_STAY_RAND)
    bill = days * (const.HOSPITAL_BILL_BASE + rng.randrange(const.HOSPITAL_BILL_RAND))
    p.debt += bill
    p.health = min(const.MAX_HEALTH, p.health + const.HOSPITAL_STAY_HEAL)
    p.status = ST_HOSPITAL
    p.hospital_days = days
    p.moved = False
    p.pending_dest = -1
    events.append(const.HOSPITAL_FORCE_TEXT.format(days=days, bill=_fmt(bill)))


def _roll_money_loss(rng: random.Random, p: Player, events: list[str]) -> None:
    """亏钱事件：按序判定，命中一条即止。公式（原版）：new = (x//100)*(100-pct)。"""
    for ev in const.MONEY_EVENTS:
        if rng.randrange(const.MISC_POOL) % ev.freq != 0:
            continue
        x = p.cash if ev.target == "cash" else p.bank
        if x > 0:
            new = (x // 100) * (100 - ev.pct)
            loss = x - new
            if ev.target == "cash":
                p.cash = new
            else:
                p.bank = new
            if loss > 0:
                events.append(ev.text.format(amount=_fmt(loss)))
        return  # 命中即止（钱不够也算"遇上了"）


def _roll_hacker(rng: random.Random, p: Player, events: list[str]) -> None:
    """黑客事件（原版公式）。"""
    if rng.randrange(const.MISC_POOL) % const.HACKER_MOD != 0:
        return
    if p.bank < const.HACKER_SMALL_LINE:
        return
    if p.bank <= const.HACKER_BIG_LINE:
        gain = p.bank // (1 + rng.randrange(15))
        p.bank += gain
        events.append(const.HACKER_GAIN_TEXT.format(amount=_fmt(gain)))
        return
    num = p.bank // (2 + rng.randrange(20))
    if rng.randrange(20) % 3 != 0:
        p.bank -= num
        events.append(const.HACKER_LOSS_TEXT.format(amount=_fmt(num)))
    else:
        p.bank += num
        events.append(const.HACKER_GAIN_TEXT.format(amount=_fmt(num)))


def _liquidate(p: Player, prices: list[int]) -> None:
    """按当日基准价强制平仓（不计市场冲击、不扣名声）。"""
    for good, h in list(p.inventory.items()):
        if prices[good] > 0:
            p.cash += h.qty * prices[good]
    p.inventory.clear()


# ---------------------------------------------------------------------------
# 结算
# ---------------------------------------------------------------------------


def standings(room: Room) -> list[dict[str, Any]]:
    """当前排名（含离场玩家）。"""
    rows = []
    for p in room.players.values():
        if p.status == ST_FINISHED:
            nw = p.final_score if p.final_score is not None else 0
        else:
            nw = p.net_worth(room.prices)
        rows.append(
            {
                "uid": p.uid,
                "name": p.name,
                "net_worth": nw,
                "status": p.status,
                "reason": p.finish_reason,
                "health": p.health,
                "fame": p.fame,
            }
        )
    rows.sort(key=lambda r: (r["reason"] == FIN_DEAD, -r["net_worth"]))
    return rows


def fame_title(fame: int) -> str:
    for line_limit, title in const.FAME_TITLES:
        if fame >= line_limit:
            return title
    return const.FAME_TITLES[-1][1]


def _achievements(p: Player) -> list[str]:
    got: list[str] = []
    score = p.final_score or 0
    by_key = {k: name for k, name, _ in const.ACHIEVEMENTS}
    if score >= 10_000_000:
        got.append(by_key["tycoon"])
    elif score >= 1_000_000:
        got.append(by_key["millionaire"])
    if p.stats.ever_debt_free:
        got.append(by_key["debt_free"])
    if p.stats.best_day_gain >= const.WINDFALL_LINE:
        got.append(by_key["windfall"])
    if p.stats.trades >= const.TRADER_LINE:
        got.append(by_key["trader"])
    if p.stats.gift_profit >= const.SCROOGE_LINE:
        got.append(by_key["scrooge"])
    if p.stats.min_health <= const.SURVIVOR_LINE:
        got.append(by_key["survivor"])
    if not p.stats.sold_shady and p.stats.trades > 0:
        got.append(by_key["clean_hands"])
    if p.stats.cafe_times >= const.CAFE_MAX_TIMES:
        got.append(by_key["net_addict"])
    if p.stats.intel_times >= const.INFORMED_LINE:
        got.append(by_key["informed"])
    return got


def _settle(room: Room, rng: random.Random) -> Settlement:
    settlement = Settlement(days_total=room.days_total)
    for p in room.players.values():
        entry = SettleEntry(
            uid=p.uid,
            name=p.name,
            reason=p.finish_reason or FIN_NORMAL,
            score=p.final_score,
            fame=p.fame,
            fame_title=fame_title(p.fame),
        )
        if p.finish_reason != FIN_DEAD:
            if (p.final_score or 0) > 0:
                entry.score_title = const.SCORE_TITLES[rng.randrange(len(const.SCORE_TITLES))]
            entry.achievements = _achievements(p)
        settlement.entries.append(entry)
    settlement.entries.sort(
        key=lambda e: (e.reason == FIN_DEAD, -(e.score if e.score is not None else 0))
    )
    return settlement


# ---------------------------------------------------------------------------
# 历史排行榜（纯函数：由调用方负责读写持久层）
# ---------------------------------------------------------------------------


def merge_leaderboard(
    board: list[dict[str, Any]],
    settlement: Settlement,
    room_label: str,
    ts: float,
) -> tuple[list[dict[str, Any]], bool]:
    """把结算结果并入排行榜；回填各 entry 的 board_rank。返回 (新榜, 是否有变化)。"""
    if not board:
        board = [
            {
                "name": const.SEED_CHAMPION["name"],
                "score": const.SEED_CHAMPION["score"],
                "title": "祖传榜首",
                "fame_title": "查无此人",
                "days": const.DEFAULT_DAYS,
                "room": "-",
                "ts": 0,
            }
        ]
    board = list(board)
    changed = False
    for e in settlement.entries:
        if e.reason == FIN_DEAD or e.score is None or e.score <= 0:
            continue
        board.append(
            {
                "name": e.name,
                "score": e.score,
                "title": e.score_title,
                "fame_title": e.fame_title,
                "days": settlement.days_total,
                "room": room_label,
                "ts": ts,
            }
        )
        changed = True
    board.sort(key=lambda r: (-r["score"], r["ts"]))
    board = board[: const.LEADERBOARD_SIZE]
    for e in settlement.entries:
        e.board_rank = None
        for i, row in enumerate(board):
            if row["name"] == e.name and row["ts"] == ts and row["score"] == e.score:
                e.board_rank = i + 1
                break
    return board, changed
