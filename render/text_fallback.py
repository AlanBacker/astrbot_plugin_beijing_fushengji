"""纯文本兜底：渲染服务不可用时把同一份上下文降级为文字消息。

输入与 HTML 模板完全同源（contexts 产出的 dict），保证两种形态内容一致。
"""

from __future__ import annotations

from typing import Any


def _market_lines(ctx: dict[str, Any]) -> list[str]:
    lines = [f"◇ 今日行情（{ctx['market_aside']}）"]
    for g in ctx["market"]:
        if g["off"]:
            lines.append(f"{g['idx']}. {g['name']}：无行情")
            continue
        marks = ""
        if g["ev"] == "up":
            marks += "🔺暴涨"
        elif g["ev"] == "down":
            marks += "🟢暴跌"
        if g["imp"] == "up":
            marks += f" ▲抬价{g['imp_pct']}"
        elif g["imp"] == "down":
            marks += f" ▼砸价{g['imp_pct']}"
        mine = f"｜持 {g['mine']}" if g.get("mine") else ""
        lines.append(f"{g['idx']}. {g['name']}：{g['price']} 元 {marks}{mine}".rstrip())
    return lines


def _card_lines(c: dict[str, Any]) -> list[str]:
    tag = f"（{c['tag']}）" if c["tag"] else ""
    return [
        f"{c['medal']} {c['name']}{tag}＠{c['loc']}｜身家 {c['nw']}",
        f"   现金 {c['cash']}｜存款 {c['bank']}｜债务 {c['debt']}｜仓库 {c['cap']}",
        f"   健康 {c['hp']}/100｜名声 {c['fame']}·{c['fame_title']}",
    ] + (
        ["   存货：" + "、".join(f"{it['name']}×{it['qty']}" for it in c["inv"])]
        if c["inv"]
        else []
    )


def _hint_lines(ctx: dict[str, Any]) -> list[str]:
    lines = []
    if ctx.get("hints"):
        lines.append(
            "◇ 常用：" + "｜".join(h["cmd"].replace("浮生记 ", "") for h in ctx["hints"][:6]) + "（命令前加「浮生记」）"
        )
    if ctx.get("locmap"):
        lines.append(
            "◇ 京城十站：" + " ".join(f"{l['idx']}{l['name']}" for l in ctx["locmap"]) + "（如「浮生记 去 3」）"
        )
    return lines


def day_text(ctx: dict[str, Any]) -> str:
    lines = [f"📰 {ctx['sub']}"]
    for n in ctx["notes"]:
        lines.append(f"※ {n}")
    if ctx["headlines"]:
        lines.append("◇ 头版快讯")
        lines += [f"● {h}" for h in ctx["headlines"]]
    for e in ctx["events"]:
        lines.append(f"◆ {e['name']}")
        lines += [f"  - {ln}" for ln in e["lines"]]
    lines += _market_lines(ctx)
    lines.append(f"◇ {ctx['cards_title']}")
    for c in ctx["cards"]:
        lines += _card_lines(c)
    lines += _hint_lines(ctx)
    return "\n".join(lines)


def panel_text(ctx: dict[str, Any]) -> str:
    lines = [f"📒 {ctx['sub']}"]
    if ctx.get("tip"):
        lines.append(f"※ {ctx['tip']}")
    for c in ctx["cards"]:
        lines += _card_lines(c)
    if ctx["inv_rows"]:
        lines.append(f"◇ 存货账本（{ctx['inv_aside']}）")
        for r in ctx["inv_rows"]:
            lines.append(
                f"  {r['name']} ×{r['qty']}｜均 {r['avg']}｜时价 {r['cur']}｜盈亏 {r['pnl']}"
            )
    lines += _market_lines(ctx)
    if ctx["headlines"]:
        lines.append("◇ 今日快讯")
        lines += [f"● {h}" for h in ctx["headlines"]]
    lines += _hint_lines(ctx)
    return "\n".join(lines)


def settle_text(ctx: dict[str, Any]) -> str:
    lines = [f"🏁 最终结算（{ctx['sub']}）"]
    for e in ctx["entries"]:
        score = e["score"] or e["score_alt"]
        lines.append(f"{e['rank']} {e['name']}｜{e['reason']}｜身家 {score}")
        extra = []
        if e["title"]:
            extra.append(f"称号「{e['title']}」")
        extra.append(f"名声 {e['fame_title']}({e['fame']})")
        if e["achievements"]:
            extra.append("成就：" + "、".join(e["achievements"]))
        lines.append("   " + "｜".join(extra))
        if e["board"]:
            lines.append(f"   🎉 {e['board']}")
    lines.append("◇ 「浮生记 创建」再开一局｜「浮生记 榜单」看历史榜")
    return "\n".join(lines)


def board_text(ctx: dict[str, Any]) -> str:
    lines = ["🏆 浮生龙虎榜（历史十强）"]
    for r in ctx["rows"]:
        lines.append(
            f"{r['rank']}. {r['name']}｜{r['score']} 元｜{r['title']}｜{r['days']}天｜{r['date']}"
        )
    return "\n".join(lines)


def help_text(ctx: dict[str, Any]) -> str:
    lines = ["📖 《北京浮生记》玩法速览"]
    lines += [f"● {h}" for h in ctx["intro"]]
    for grp in ctx["groups"]:
        lines.append(f"◇ {grp['title']}")
        lines += [f"  {c['cmd']}｜{c['desc']}" for c in grp["cmds"]]
    lines.append(
        "◇ 京城十站：" + " ".join(f"{l['idx']}{l['name']}" for l in ctx["locs"]) + f"（{ctx['locs_aside']}）"
    )
    lines.append("◇ 货品（常见价位）")
    lines += [f"  {g['idx']}. {g['name']}：{g['range']}（{g['note']}）" for g in ctx["goods"]]
    return "\n".join(lines)
