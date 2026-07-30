"""玩家输入解析：商品、地点、数量/金额的中文容错识别。

统一约定：解析失败抛 GameError（带用法提示）；"全部"返回 engine.ALL(None)。
"""

from __future__ import annotations

from . import const
from .errors import GameError

ALL_WORDS = {"全", "全部", "所有", "all", "max", "梭哈"}
HALF_WORDS = {"半", "一半", "half"}


def _norm(token: str) -> str:
    return token.strip().lower()


def parse_good(token: str) -> int:
    """商品：支持序号(1-8)、简称、全名、别名、前缀匹配。"""
    t = _norm(token)
    if not t:
        raise GameError("要指定商品。", "例如「浮生记 买 手机 10」或「浮生记 买 7 全」")
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < const.N_GOODS:
            return idx
        raise GameError(f"商品序号是 1~{const.N_GOODS}。", "发送「浮生记 面板」查看行情表。")
    for i, g in enumerate(const.GOODS):
        names = {g.name.lower(), g.short.lower(), *(a.lower() for a in g.aliases)}
        if t in names:
            return i
    # 前缀/包含匹配兜底（如"盗版"、"化妆"）
    hits = [
        i
        for i, g in enumerate(const.GOODS)
        if t in g.name.lower() or t in g.short.lower()
    ]
    if len(hits) == 1:
        return hits[0]
    raise GameError(f"不认识的商品「{token}」。", "发送「浮生记 面板」查看商品名与序号。")


def parse_location(token: str) -> int:
    """地点：支持序号(1-10)、全名、前缀。"""
    t = _norm(token)
    menu = f"京城十站：{const.LOCATION_LINE}"
    if not t:
        raise GameError("要指定去哪儿。", f"{menu}；如「浮生记 去 3」或「浮生记 去 西直门」。")
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(const.LOCATIONS):
            return idx
        raise GameError(f"地点序号是 1~{len(const.LOCATIONS)}。", f"{menu}。")
    for i, name in enumerate(const.LOCATIONS):
        if t == name.lower():
            return i
    hits = [i for i, name in enumerate(const.LOCATIONS) if name.lower().startswith(t)]
    if len(hits) == 1:
        return hits[0]
    raise GameError(f"没有「{token}」这一站。", f"{menu}。")


def parse_qty(token: str) -> int | None:
    """件数：正整数或"全"。"""
    t = _norm(token)
    if t in ALL_WORDS:
        return None
    if t.isdigit():
        n = int(t)
        if n > 0:
            return n
    raise GameError(f"数量「{token}」看不懂。", "用正整数或「全」。")


def parse_money(token: str, base_for_half: int = 0) -> int | None:
    """金额：正整数、带"万"（支持小数如 1.5万）、"全"、"半"。

    "半"按 base_for_half（调用方给出的基数，如现金）的一半折算。
    """
    t = _norm(token).replace(",", "").replace("，", "")
    if t in ALL_WORDS:
        return None
    if t in HALF_WORDS:
        n = base_for_half // 2
        if n <= 0:
            raise GameError("一半也凑不出一块钱。")
        return n
    mult = 1
    if t.endswith("万"):
        mult = 10000
        t = t[:-1]
    elif t.endswith("w"):
        mult = 10000
        t = t[:-1]
    try:
        value = float(t) if "." in t else int(t)
    except ValueError:
        raise GameError(f"金额「{token}」看不懂。", "用正整数、「2万」「1.5万」或「全」「半」。") from None
    n = int(value * mult)
    if n <= 0:
        raise GameError("金额得是正数。")
    return n
