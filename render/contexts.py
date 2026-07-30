"""模板上下文构建：把游戏状态整理成模板需要的纯数据。

只输出 str/int/list/dict（可直接 JSON 化 POST 给渲染端），不含 HTML；
玩家昵称统一经 clean_name 消毒，避免奇怪字符破坏版面。
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..core import const, market
from ..core.engine import DayReport, Settlement, fame_title
from ..core.models import (
    FIN_DEAD,
    FIN_NORMAL,
    FIN_SURRENDER,
    ST_ACTIVE,
    ST_FINISHED,
    ST_HOSPITAL,
    Player,
    Room,
)

RANK_LABELS = ["状元", "榜眼", "探花", "传胪"]

REASON_LABELS = {
    FIN_NORMAL: "圆满收官",
    FIN_SURRENDER: "中途收手",
    FIN_DEAD: "客死他乡",
}

_NAME_BAD = str.maketrans({"<": "＜", ">": "＞", "&": "＆", '"': "＂", "'": "＇", "`": "｀"})


def fmt(n: int) -> str:
    return f"{n:,}"


def clean_name(raw: str) -> str:
    """昵称消毒：去控制字符、转义敏感符号、限长。"""
    s = re.sub(r"[\s  ]+", " ", str(raw)).strip().translate(_NAME_BAD)
    s = "".join(ch for ch in s if ch.isprintable())
    if not s:
        return "无名氏"
    return s[:11] + "…" if len(s) > 12 else s


# ---------------------------------------------------------------------------
# 公共片段
# ---------------------------------------------------------------------------

HINTS_PLAY = [
    {"cmd": "浮生记 买 <货|序号> <数|全>", "desc": "进货"},
    {"cmd": "浮生记 卖 <货|序号> <数|全>", "desc": "出货"},
    {"cmd": "浮生记 去 <地点|序号>", "desc": "赶路，开启新一天"},
    {"cmd": "浮生记 留守", "desc": "原地待一天"},
    {"cmd": "浮生记 面板", "desc": "看自己的账本"},
    {"cmd": "浮生记 存 全 / 取 <钱>", "desc": "银行(日息1%)"},
    {"cmd": "浮生记 还 <钱|全>", "desc": "邮局还债(日息10%)"},
    {"cmd": "浮生记 看病 <点|全>", "desc": "医院(3,500/点)"},
    {"cmd": "浮生记 网吧 / 情报", "desc": "打零工·买消息"},
    {"cmd": "浮生记 帮助", "desc": "完整说明书"},
]

HINTS_AFTER = [
    {"cmd": "浮生记 创建 [天数]", "desc": "再开一局"},
    {"cmd": "浮生记 榜单", "desc": "历史龙虎榜"},
    {"cmd": "浮生记 帮助", "desc": "玩法说明书"},
]

# 京城十站速查（对局中的版面都带上，玩家不用翻说明书）
LOC_CHIPS = [{"idx": i + 1, "name": n} for i, n in enumerate(const.LOCATIONS)]


def _market_rows(room: Room, viewer: Player | None) -> list[dict[str, Any]]:
    impact_on = bool(room.setting("market_impact", True))
    rows: list[dict[str, Any]] = []
    for i, g in enumerate(const.GOODS):
        price = market.effective_price(room, i, impact_on)
        steps = market.impact_steps(room, i) if impact_on else 0
        mark = room.price_marks[i]
        mine = ""
        if viewer is not None:
            h = viewer.inventory.get(i)
            if h and h.qty > 0:
                mine = f"×{h.qty} · 均 {fmt(h.avg_cost)}"
        rows.append(
            {
                "idx": i + 1,
                "name": g.name,
                "fame": g.fame_cost > 0,
                "off": price <= 0,
                "price": fmt(price),
                "ev": "up" if mark > 0 else ("down" if mark < 0 else ""),
                "imp": "up" if steps > 0 else ("down" if steps < 0 else ""),
                "imp_pct": f"{abs(steps) * const.IMPACT_STEP_PCT}%",
                "mine": mine,
            }
        )
    return rows


def _status_tag(p: Player) -> str:
    if p.status == ST_HOSPITAL:
        return f"住院·剩{p.hospital_days}天"
    if p.status == ST_FINISHED:
        return {FIN_DEAD: "身故", FIN_SURRENDER: "离场"}.get(p.finish_reason, "收官")
    if p.moved:
        return "已行动"
    return ""


def _player_card(room: Room, p: Player, rank: int) -> dict[str, Any]:
    dead = p.finish_reason == FIN_DEAD
    if p.status == ST_FINISHED:
        nw = "——" if p.final_score is None else fmt(p.final_score)
        loc = "已回乡"
    else:
        nw = fmt(p.net_worth(room.prices))
        loc = "医院" if p.status == ST_HOSPITAL else const.LOCATIONS[p.location]
    hp = max(0, min(const.MAX_HEALTH, p.health))
    return {
        "medal": "☠" if dead else RANK_LABELS[min(rank, len(RANK_LABELS) - 1)],
        "dead": dead,
        "name": clean_name(p.name),
        "loc": loc,
        "tag": _status_tag(p),
        "dim": p.status == ST_FINISHED,
        "nw": nw,
        "cash": fmt(p.cash),
        "bank": fmt(p.bank),
        "debt": fmt(p.debt),
        "debt_hot": p.debt > const.THUG_DEBT_LINE,
        "cap": f"{p.used_capacity()}/{p.capacity}",
        "hp": hp,
        "hp_pct": hp,
        "hp_cls": "ok" if hp >= 40 else "low",
        "fame": p.fame,
        "fame_pct": max(0, min(100, p.fame)),
        "fame_title": fame_title(p.fame),
        "inv": [
            {"name": const.GOODS[g].short, "qty": h.qty}
            for g, h in sorted(p.inventory.items())
        ],
    }


def _cards_by_standing(room: Room) -> list[dict[str, Any]]:
    from ..core.engine import standings  # 局部导入避免环

    rows = standings(room)
    return [_player_card(room, room.players[r["uid"]], i) for i, r in enumerate(rows)]


def _market_aside(room: Room) -> str:
    left = room.days_left()
    if left == 0:
        return "最后一天 · 明早强制平仓"
    return f"距收摊还有 {left} 天"


# ---------------------------------------------------------------------------
# 各版面上下文
# ---------------------------------------------------------------------------


def day_context(room: Room, reports: list[DayReport]) -> dict[str, Any]:
    """日报：一次操作可能推进多天（全员住院快进），合并展示。"""
    last = reports[-1]
    notes = list(last.notes)
    events: list[dict[str, Any]] = []
    if len(reports) > 1:
        first_day = reports[0].day
        notes.insert(0, f"全员卧床的第 {first_day}~{last.day - 1} 天一晃而过，京城的日子照常翻篇。")
        merged: dict[str, list[str]] = {}
        for r in reports:
            for name, lines in r.player_events:
                prefix = f"(第{r.day}天) " if r is not last else ""
                merged.setdefault(name, []).extend(prefix + ln for ln in lines)
        events = [{"name": clean_name(n), "lines": ls} for n, ls in merged.items()]
    else:
        events = [
            {"name": clean_name(n), "lines": list(ls)} for n, ls in last.player_events
        ]

    n_in = len(room.in_game_players())
    return {
        "kicker": "每 日 快 报",
        "sub": f"第 {last.day} 天 / 共 {last.days_total} 天 · 在局 {n_in} 人",
        "slogan_left": "债息 10%/日 · 存息 1%/日",
        "slogan_right": "低买高卖 · 快进快出",
        "headlines": list(last.headlines),
        "notes": notes,
        "events": events,
        "market": _market_rows(room, None),
        "market_aside": _market_aside(room),
        "show_mine": False,
        "cards": _cards_by_standing(room),
        "cards_title": "群雄座次",
        "cards_aside": "按身家排座（身家 = 现金+存款+存货-债务）",
        "hints": HINTS_PLAY,
        "locmap": LOC_CHIPS,
        "foot_left": f"《北京浮生记》 第 {last.day}/{last.days_total} 天",
    }


def rank_context(room: Room) -> dict[str, Any]:
    """排行视图：复用日报版面，只留行情与座次。"""
    n_in = len(room.in_game_players())
    return {
        "kicker": "座 次 号 外",
        "sub": f"第 {room.day} 天 / 共 {room.days_total} 天 · 在局 {n_in} 人",
        "slogan_left": "债息 10%/日 · 存息 1%/日",
        "slogan_right": "三十年河东 · 三十年河西",
        "headlines": list(room.headlines),
        "notes": [],
        "events": [],
        "market": _market_rows(room, None),
        "market_aside": _market_aside(room),
        "show_mine": False,
        "cards": _cards_by_standing(room),
        "cards_title": "群雄座次",
        "cards_aside": "按身家排座（身家 = 现金+存款+存货-债务）",
        "hints": HINTS_PLAY,
        "locmap": LOC_CHIPS,
        "foot_left": f"《北京浮生记》 第 {room.day}/{room.days_total} 天",
    }


def panel_context(room: Room, uid: str) -> dict[str, Any]:
    p = room.players[uid]
    name = clean_name(p.name)
    impact_on = bool(room.setting("market_impact", True))

    inv_rows = []
    for g, h in sorted(p.inventory.items()):
        cur = market.effective_price(room, g, impact_on)
        if cur > 0:
            pnl = (cur - h.avg_cost) * h.qty
            sign = "+" if pnl >= 0 else "−"
            row = {
                "cur": fmt(cur),
                "pnl": f"{sign}{fmt(abs(pnl))}",
                "pnl_cls": "pnl-up" if pnl >= 0 else "pnl-down",
            }
        else:
            row = {"cur": "—", "pnl": "—", "pnl_cls": ""}
        inv_rows.append(
            {"name": const.GOODS[g].name, "qty": h.qty, "avg": fmt(h.avg_cost), **row}
        )

    tip = ""
    if room.tip is not None and p.intel_day == room.day:
        g = const.GOODS[room.tip.good]
        trend = "有大动静，指定暴涨" if room.tip.is_up else "要栽大跟头，趁早出手"
        tip = f"你今天买到的小道消息：明儿{g.name}{trend}。准头几成，自个儿掂量。"

    where = "医院" if p.status == ST_HOSPITAL else const.LOCATIONS[p.location]
    return {
        "kicker": "随 身 账 本",
        "sub": f"{name} · 第 {room.day} 天 / 共 {room.days_total} 天 · 身在{where}",
        "slogan_left": "债息 10%/日 · 存息 1%/日",
        "slogan_right": "闷声发财 · 落袋为安",
        "tip": tip,
        "cards": [_player_card(room, p, _rank_of(room, uid))],
        "cards_title": "身家档案",
        "cards_aside": f"第 {room.day} 天 · 以当前时价折算",
        "inv_rows": inv_rows,
        "inv_aside": f"占用仓位 {p.used_capacity()}/{p.capacity}",
        "market": _market_rows(room, p),
        "market_aside": _market_aside(room),
        "show_mine": True,
        "headlines": list(room.headlines),
        "notes": [],
        "hints": HINTS_PLAY,
        "locmap": LOC_CHIPS,
        "foot_left": f"《北京浮生记》 {name} 的账本",
    }


def _rank_of(room: Room, uid: str) -> int:
    from ..core.engine import standings

    for i, r in enumerate(standings(room)):
        if r["uid"] == uid:
            return i
    return 0


def settle_context(settlement: Settlement) -> dict[str, Any]:
    entries = []
    alive_rank = 0
    for e in settlement.entries:
        dead = e.reason == FIN_DEAD
        if dead:
            rank = "☠"
        else:
            rank = RANK_LABELS[min(alive_rank, len(RANK_LABELS) - 1)]
            alive_rank += 1
        entries.append(
            {
                "rank": rank,
                "name": clean_name(e.name),
                "reason": REASON_LABELS.get(e.reason, e.reason),
                "score": "" if e.score is None else fmt(e.score),
                "score_alt": "身故无算",
                "neg": (e.score or 0) < 0,
                "champ": (e.score or 0) > 0,
                "title": e.score_title,
                "fame": e.fame,
                "fame_title": e.fame_title,
                "achievements": list(e.achievements),
                "board": (
                    f"杀进浮生龙虎榜第 {e.board_rank} 名！" if e.board_rank else ""
                ),
                "epitaph": (
                    "京城依旧车水马龙，没人记得他来过。" if dead else ""
                ),
            }
        )
    return {
        "kicker": "结 算 特 刊",
        "sub": f"{settlement.days_total} 天浮生 · 一朝清账",
        "slogan_left": "身家 = 现金 + 存款 − 债务",
        "slogan_right": "愿赌服输 · 概不赊账",
        "settle_aside": "破产不上榜 · 身故不入流",
        "entries": entries,
        "hints": HINTS_AFTER,
        "foot_left": "《北京浮生记》 结算特刊",
    }


def board_context(board: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for i, r in enumerate(board):
        ts = r.get("ts") or 0
        rows.append(
            {
                "rank": i + 1,
                "name": clean_name(r.get("name", "?")),
                "score": fmt(int(r.get("score", 0))),
                "title": r.get("title") or "—",
                "fame_title": r.get("fame_title") or "—",
                "days": r.get("days", "—"),
                "date": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "上古",
            }
        )
    return {
        "kicker": "历 史 龙 虎 榜",
        "sub": "各路豪杰 · 谁与争锋",
        "slogan_left": "只认身家 · 不认人",
        "slogan_right": "长江后浪推前浪",
        "rows": rows,
        "hints": HINTS_AFTER,
        "foot_left": "《北京浮生记》 龙虎榜",
    }


def help_context() -> dict[str, Any]:
    goods_notes = {
        0: "小本快销",
        1: "大件压仓，波动也大",
        2: "蚊子腿也是肉",
        3: "卖一笔名声 −10",
        4: "卖一笔名声 −7",
        5: "常有暴涨行情",
        6: "毕业季的硬通货",
        7: "便宜，翻倍狠",
    }
    goods = [
        {
            "idx": i + 1,
            "name": g.name,
            "range": f"{fmt(g.base)} ~ {fmt(g.base + g.rand_range - 1)}",
            "note": goods_notes.get(i, ""),
        }
        for i, g in enumerate(const.GOODS)
    ]
    groups = [
        {
            "title": "组局",
            "aside": "群里最多 4 人同局",
            "cmds": [
                {"cmd": "浮生记 创建 [天数]", "desc": "开新局(默认40天)"},
                {"cmd": "浮生记 加入", "desc": "上车(开局前)"},
                {"cmd": "浮生记 开始", "desc": "房主发车"},
                {"cmd": "浮生记 跳过", "desc": "房主催场磨蹭的人"},
                {"cmd": "浮生记 认输", "desc": "提前清算离场"},
                {"cmd": "浮生记 解散 确认", "desc": "房主解散本局"},
            ],
        },
        {
            "title": "每天",
            "aside": "全员「去/留守」后进入新一天",
            "cmds": [
                {"cmd": "浮生记 去 <地点|序号>", "desc": "赶路(锁定当天)"},
                {"cmd": "浮生记 留守", "desc": "原地过一天"},
                {"cmd": "浮生记 买 <货|序号> <数|全>", "desc": "进货"},
                {"cmd": "浮生记 卖 <货|序号> <数|全>", "desc": "出货"},
                {"cmd": "浮生记 面板", "desc": "自己的账本与行情"},
                {"cmd": "浮生记 排行", "desc": "本局当前座次"},
            ],
        },
        {
            "title": "周转",
            "aside": "债是利滚利的，别攒",
            "cmds": [
                {"cmd": "浮生记 存 <钱|全|半>", "desc": "存银行吃 1% 日息"},
                {"cmd": "浮生记 取 <钱|全>", "desc": "取存款"},
                {"cmd": "浮生记 还 <钱|全>", "desc": "还高利贷(10%日息)"},
                {"cmd": "浮生记 看病 <点|全>", "desc": "3,500 元/点补健康"},
                {"cmd": "浮生记 租房", "desc": "仓库 +10(要现金3万+)"},
                {"cmd": "浮生记 网吧 / 情报", "desc": "打零工·买明日消息"},
            ],
        },
    ]
    intro = [
        "你揣着 2,000 元现金、背着 5,500 元高利贷进京，限期内倒腾出一份身家。",
        "每天各站行情重开：低买高卖，遇上「暴涨」的新闻就是发财的日子。",
        "健康扣光人就没了；债过 10 万，村长的打手天天上门；名声太臭，遗臭万年。",
        "多人同局共享行情：你扫货会抬价，你抛售会砸盘，勾心斗角，各凭本事。",
    ]
    return {
        "kicker": "创 刊 号",
        "sub": "玩法说明书 · 致敬 2001 年经典《北京浮生记》",
        "slogan_left": "命令都以「浮生记」开头",
        "slogan_right": "也可用「fs」「浮生」简写",
        "intro": intro,
        "groups": groups,
        "locs": LOC_CHIPS,
        "locs_aside": "「去 序号」或「去 站名」均可；行情每天重开，与身在哪站无关",
        "goods": goods,
        "hints": [],
        "foot_left": "《北京浮生记》 说明书",
    }
