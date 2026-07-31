"""游戏状态模型与序列化。

只做数据承载与 dict <-> 对象转换，不含游戏规则；规则见 engine。
序列化格式为纯 JSON 可表示类型，供 store 落盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import const

# 房间阶段
PHASE_SIGNUP = "signup"
PHASE_RUNNING = "running"

# 玩家状态
ST_ACTIVE = "active"
ST_HOSPITAL = "hospital"
ST_FINISHED = "finished"

# 离场原因
FIN_NORMAL = "normal"  # 玩到最后一天
FIN_DEAD = "dead"  # 健康 < 0
FIN_SURRENDER = "surrender"  # 认输提前结算


@dataclass
class Holding:
    qty: int = 0
    avg_cost: int = 0  # 加权平均进价（白捡的货按 0 计）


@dataclass
class PlayerStats:
    """行为统计，用于结算成就。"""

    trades: int = 0  # 成交笔数（买+卖）
    best_day_gain: int = 0  # 单日净资产最大增幅
    gift_profit: int = 0  # 卖出 0 成本货的累计净赚
    min_health: int = const.START_HEALTH
    ever_debt_free: bool = False
    sold_shady: bool = False  # 卖过假白酒/假古董
    cafe_times: int = 0
    intel_times: int = 0
    boom_seen: int = 0  # 在场天数里赶上的景气点（结算评行情用）
    days_active: int = 0  # 能自由行动的天数（住院/离场不计）

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "best_day_gain": self.best_day_gain,
            "gift_profit": self.gift_profit,
            "min_health": self.min_health,
            "ever_debt_free": self.ever_debt_free,
            "sold_shady": self.sold_shady,
            "cafe_times": self.cafe_times,
            "intel_times": self.intel_times,
            "boom_seen": self.boom_seen,
            "days_active": self.days_active,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlayerStats":
        s = cls()
        for k in s.to_dict():
            if k in d:
                setattr(s, k, d[k])
        return s


@dataclass
class Player:
    uid: str
    name: str
    cash: int = const.START_CASH
    bank: int = 0
    debt: int = const.START_DEBT
    health: int = const.START_HEALTH
    fame: int = const.START_FAME
    capacity: int = const.START_CAPACITY
    location: int = const.START_LOCATION
    inventory: dict[int, Holding] = field(default_factory=dict)
    status: str = ST_ACTIVE
    hospital_days: int = 0  # 剩余住院天数
    moved: bool = False  # 今天是否已行动（去/留守）
    pending_dest: int = -1  # 已锁定的明日目的地（-1 表示留守原地）
    finish_reason: str = ""
    final_score: int | None = None
    intel_day: int = 0  # 最近一次买情报的天数（每天限一次）
    net_worth_yesterday: int = 0  # 昨日净资产（算单日增幅用）
    stats: PlayerStats = field(default_factory=PlayerStats)

    # ---- 派生量 ----

    def used_capacity(self) -> int:
        return sum(h.qty for h in self.inventory.values())

    def capacity_left(self) -> int:
        return self.capacity - self.used_capacity()

    def stock_value(self, prices: list[int]) -> int:
        """按给定价格表估算存货市值（未上市按 0 计）。"""
        return sum(h.qty * prices[g] for g, h in self.inventory.items() if prices[g] > 0)

    def net_worth(self, prices: list[int]) -> int:
        return self.cash + self.bank - self.debt + self.stock_value(prices)

    def in_game(self) -> bool:
        return self.status in (ST_ACTIVE, ST_HOSPITAL)

    # ---- 序列化 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "name": self.name,
            "cash": self.cash,
            "bank": self.bank,
            "debt": self.debt,
            "health": self.health,
            "fame": self.fame,
            "capacity": self.capacity,
            "location": self.location,
            "inventory": {str(g): [h.qty, h.avg_cost] for g, h in self.inventory.items()},
            "status": self.status,
            "hospital_days": self.hospital_days,
            "moved": self.moved,
            "pending_dest": self.pending_dest,
            "finish_reason": self.finish_reason,
            "final_score": self.final_score,
            "intel_day": self.intel_day,
            "net_worth_yesterday": self.net_worth_yesterday,
            "stats": self.stats.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Player":
        p = cls(uid=d["uid"], name=d["name"])
        for k in (
            "cash", "bank", "debt", "health", "fame", "capacity", "location",
            "hospital_days", "moved", "pending_dest", "finish_reason",
            "final_score", "intel_day", "net_worth_yesterday", "status",
        ):
            if k in d:
                setattr(p, k, d[k])
        p.inventory = {
            int(g): Holding(qty=v[0], avg_cost=v[1])
            for g, v in d.get("inventory", {}).items()
            if v[0] > 0
        }
        p.stats = PlayerStats.from_dict(d.get("stats", {}))
        return p


@dataclass
class Tip:
    """网吧情报：预言明日某商品的涨/跌。"""

    good: int
    is_up: bool
    truthful: bool  # 真消息 -> 明日强制触发对应价格事件
    event_idx: int  # 真消息对应 PRICE_EVENTS 下标；假消息为 -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "good": self.good,
            "is_up": self.is_up,
            "truthful": self.truthful,
            "event_idx": self.event_idx,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tip":
        return cls(d["good"], d["is_up"], d["truthful"], d["event_idx"])


@dataclass
class Room:
    room_id: str
    creator: str
    phase: str = PHASE_SIGNUP
    day: int = 0  # 1..days_total；signup 阶段为 0
    days_total: int = const.DEFAULT_DAYS
    players: dict[str, Player] = field(default_factory=dict)  # 保持加入顺序
    prices: list[int] = field(default_factory=lambda: [0] * const.N_GOODS)  # 0=未上市
    impact_flow: list[int] = field(default_factory=lambda: [0] * const.N_GOODS)
    price_marks: list[int] = field(default_factory=lambda: [0] * const.N_GOODS)  # 1涨/-1跌
    headlines: list[str] = field(default_factory=list)  # 今日新闻
    tip: Tip | None = None  # 已购买、指向明日的情报
    boom_total: int = 0  # 本局累计景气点（价格事件带来的机遇总量）
    day_started_at: float = 0.0  # 当天开始的 unix 时间（闲置超时用）
    settings: dict[str, Any] = field(default_factory=dict)  # 创建时的配置快照

    # ---- 便捷方法 ----

    def in_game_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.in_game()]

    def active_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.status == ST_ACTIVE]

    def waiting_players(self) -> list[Player]:
        """今天还没行动的存活玩家。"""
        return [p for p in self.active_players() if not p.moved]

    def days_left(self) -> int:
        """今天过完后还剩几天。"""
        return self.days_total - self.day

    def is_full_market_day(self) -> bool:
        return self.days_left() < const.FULL_MARKET_LAST_DAYS

    def setting(self, key: str, default: Any) -> Any:
        return self.settings.get(key, default)

    # ---- 序列化 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "creator": self.creator,
            "phase": self.phase,
            "day": self.day,
            "days_total": self.days_total,
            "players": [p.to_dict() for p in self.players.values()],
            "prices": list(self.prices),
            "impact_flow": list(self.impact_flow),
            "price_marks": list(self.price_marks),
            "headlines": list(self.headlines),
            "tip": self.tip.to_dict() if self.tip else None,
            "boom_total": self.boom_total,
            "day_started_at": self.day_started_at,
            "settings": dict(self.settings),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Room":
        room = cls(room_id=d["room_id"], creator=d["creator"])
        for k in ("phase", "day", "days_total", "boom_total", "day_started_at"):
            if k in d:
                setattr(room, k, d[k])
        room.players = {pd["uid"]: Player.from_dict(pd) for pd in d.get("players", [])}
        prices = d.get("prices", [])
        flow = d.get("impact_flow", [])
        marks = d.get("price_marks", [])
        room.prices = [int(x) for x in prices] + [0] * (const.N_GOODS - len(prices))
        room.impact_flow = [int(x) for x in flow] + [0] * (const.N_GOODS - len(flow))
        room.price_marks = [int(x) for x in marks] + [0] * (const.N_GOODS - len(marks))
        room.headlines = list(d.get("headlines", []))
        room.tip = Tip.from_dict(d["tip"]) if d.get("tip") else None
        room.settings = dict(d.get("settings", {}))
        return room
