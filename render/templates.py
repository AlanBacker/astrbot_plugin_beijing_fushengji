"""Jinja2 HTML 模板：《京城浮生小报》九十年代小报风。

约定：
    - 模板数据只含纯文本/数字/列表/字典，不含任何 HTML 片段，
      因此无论渲染端是否开启 autoescape，输出都一致且安全；
    - 所有数字在 contexts 层预格式化为带千分位的字符串；
    - 页面宽 760px，配色为旧报纸的纸黄 + 墨黑 + 朱砂红（涨红跌绿）；
    - <meta name="viewport"> 声明 760x240 视口：AstrBot 官方 t2i 服务会按它设定
      截图视口，配合 full_page 让成图恰好贴住版面（宽 760，高随内容伸展），
      与 render/__init__.py 里显式下发的 viewport_width/height 互为双保险。
"""

from __future__ import annotations

VIEWPORT_WIDTH = 760  # 与 body 宽一致 -> 成图两侧无留白
VIEWPORT_HEIGHT = 240  # 远小于任何版面高度 -> full_page 高度=内容实际高度

# ---------------------------------------------------------------------------
# 公共样式与骨架
# ---------------------------------------------------------------------------

_CSS = """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body {
    background:
      radial-gradient(ellipse at 20% 0%, rgba(255,255,255,.45), rgba(255,255,255,0) 55%),
      repeating-linear-gradient(0deg, rgba(120,95,55,.035) 0 2px, rgba(0,0,0,0) 2px 4px),
      linear-gradient(160deg, #f6efdc 0%, #f1e7cf 55%, #eadfc4 100%);
    background-color: #f3ead4;
  }
  body {
    width: 760px; margin: 0 auto;
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Noto Serif SC",
                 "STSong", "SimSun", "Noto Sans CJK SC", "Microsoft YaHei", serif;
    color: #2b2620; font-size: 15px; line-height: 1.65;
  }
  .paper { position: relative; margin: 0 auto; padding: 16px 18px 14px; }
  .frame { border: 3px double #2b2620; padding: 18px 22px 20px; }

  /* ---- 报头 ---- */
  .masthead { text-align: center; margin-bottom: 6px; }
  .mh-kicker {
    display: inline-block; color: #a53326; font-weight: 700; font-size: 13px;
    letter-spacing: 6px; border-top: 1px solid #a53326; border-bottom: 1px solid #a53326;
    padding: 1px 10px; margin-bottom: 6px;
  }
  .mh-title { font-size: 44px; font-weight: 900; letter-spacing: 10px; line-height: 1.25; }
  .mh-title .mh-mark { color: #a53326; }
  .mh-sub { margin-top: 4px; font-size: 14px; letter-spacing: 2px; color: #4a4238; }
  .mh-sub b { color: #2b2620; }
  .mh-rule { margin: 10px 0 0; border-top: 3px double #2b2620; position: relative; }
  .mh-slogan {
    display: flex; justify-content: space-between; font-size: 12px; color: #6b5f4d;
    letter-spacing: 1px; padding: 3px 2px 0;
  }

  /* ---- 版块 ---- */
  .sec { margin-top: 16px; }
  .sec-h {
    display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
  }
  .sec-h .tag {
    background: #2b2620; color: #f3ead4; font-weight: 700; font-size: 15px;
    letter-spacing: 4px; padding: 2px 12px 3px;
  }
  .sec-h .tag.red { background: #a53326; }
  .sec-h .line { flex: 1; border-top: 1px solid #2b2620; opacity: .55; }
  .sec-h .aside { font-size: 12px; color: #6b5f4d; letter-spacing: 1px; }

  /* ---- 头条 ---- */
  .headlines { }
  .hl { padding: 4px 2px; display: flex; gap: 8px; align-items: baseline; }
  .hl + .hl { border-top: 1px dashed rgba(43,38,32,.35); }
  .hl .dot { color: #a53326; font-weight: 900; }
  .hl.first { font-size: 17px; font-weight: 700; }
  .hl.calm { color: #6b5f4d; }

  /* ---- 行情表 ---- */
  table.mkt { width: 100%; border-collapse: collapse; }
  table.mkt th {
    font-size: 13px; letter-spacing: 2px; color: #4a4238; font-weight: 700;
    border-top: 1px solid #2b2620; border-bottom: 2px solid #2b2620;
    padding: 5px 6px; text-align: left;
  }
  table.mkt td { padding: 6px 6px; border-bottom: 1px solid rgba(43,38,32,.28); vertical-align: middle; }
  table.mkt tr:last-child td { border-bottom: 1px solid #2b2620; }
  .g-idx { color: #6b5f4d; font-size: 13px; width: 30px; }
  .g-name { font-weight: 700; }
  .g-name small { font-weight: 400; color: #a53326; font-size: 12px; }
  .g-price { font-size: 19px; font-weight: 800; text-align: right; width: 110px;
             font-variant-numeric: tabular-nums; }
  .g-price small { font-size: 12px; font-weight: 400; color: #6b5f4d; }
  .g-flag { width: 150px; }
  .g-mine { text-align: right; font-size: 13px; color: #4a4238; width: 140px; }
  tr.off td { color: #8d8272; }
  tr.off .g-price { font-size: 14px; font-weight: 400; letter-spacing: 2px; }
  .b {
    display: inline-block; font-size: 12px; font-weight: 700; line-height: 1;
    padding: 3px 6px; border-radius: 2px; margin-right: 4px; letter-spacing: 1px;
  }
  .b-up   { background: #a53326; color: #f6efdc; }
  .b-down { background: #2f6d3a; color: #f6efdc; }
  .b-imp-up   { border: 1px solid #a53326; color: #a53326; }
  .b-imp-down { border: 1px solid #2f6d3a; color: #2f6d3a; }
  .b-tip { border: 1px dashed #8a6d3b; color: #8a6d3b; }

  /* ---- 玩家卡 ---- */
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .cards.single { grid-template-columns: 1fr; }
  .card { border: 1px solid #2b2620; padding: 8px 12px 10px; background: rgba(255,252,240,.45); }
  .card.dim { opacity: .62; }
  .card-h { border-bottom: 1px solid rgba(43,38,32,.4); padding-bottom: 6px; margin-bottom: 7px; }
  .ch-top { display: flex; align-items: center; gap: 8px; }
  .ch-sub { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
  .rank-medal {
    background: #a53326; color: #f6efdc; font-size: 13px; font-weight: 700;
    padding: 2px 7px; letter-spacing: 2px; white-space: nowrap; border-radius: 2px;
  }
  .rank-medal.dead { background: #2b2620; }
  .p-name { font-size: 17px; font-weight: 800; letter-spacing: 1px; flex: 1;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .p-loc { font-size: 12px; color: #f6efdc; background: #6b5f4d; padding: 1px 8px;
           border-radius: 2px; white-space: nowrap; }
  .p-tag { font-size: 12px; color: #a53326; border: 1px solid #a53326; padding: 0 6px;
           border-radius: 2px; white-space: nowrap; }
  .p-nw { text-align: right; white-space: nowrap; }
  .p-nw b { font-size: 18px; font-variant-numeric: tabular-nums; }
  .p-nw small { display: block; font-size: 11px; color: #6b5f4d; letter-spacing: 2px; }
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 18px; font-size: 13.5px; }
  .stat { display: flex; justify-content: space-between; gap: 8px; }
  .stat .k { color: #6b5f4d; letter-spacing: 2px; }
  .stat .v { font-weight: 700; font-variant-numeric: tabular-nums; }
  .stat .v.neg { color: #a53326; }
  .barline { display: flex; align-items: center; gap: 6px; font-size: 13.5px; margin-top: 3px; }
  .barline .k { color: #6b5f4d; letter-spacing: 2px; white-space: nowrap; }
  .bar { flex: 1; height: 9px; border: 1px solid #2b2620; background: rgba(43,38,32,.08); }
  .bar i { display: block; height: 100%; }
  .bar i.ok { background: #4a683d; }
  .bar i.low { background: #a53326; }
  .bar i.fame { background: #8a6d3b; }
  .barline .v { font-weight: 700; font-size: 12.5px; min-width: 52px; text-align: right;
                font-variant-numeric: tabular-nums; white-space: nowrap; }
  .inv { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }
  .inv-chip {
    font-size: 12.5px; border: 1px solid rgba(43,38,32,.55); padding: 1px 7px;
    border-radius: 2px; background: rgba(255,252,240,.6);
  }
  .inv-chip b { font-size: 13px; }
  .inv-empty { font-size: 12.5px; color: #8d8272; }

  /* ---- 街头见闻 ---- */
  .ev-p { margin-bottom: 8px; }
  .ev-name { font-weight: 800; }
  .ev-name::before { content: "◆ "; color: #a53326; }
  .ev-li { padding-left: 20px; font-size: 14px; }
  .ev-li::before { content: "— "; color: #6b5f4d; }

  /* ---- 明细表（面板持仓） ---- */
  table.inv-t { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.inv-t th { font-size: 12.5px; color: #4a4238; letter-spacing: 2px; text-align: left;
                   border-bottom: 2px solid #2b2620; padding: 3px 6px; }
  table.inv-t td { padding: 4px 6px; border-bottom: 1px solid rgba(43,38,32,.25);
                   font-variant-numeric: tabular-nums; }
  .pnl-up { color: #a53326; font-weight: 700; }
  .pnl-down { color: #2f6d3a; font-weight: 700; }

  /* ---- 榜单 ---- */
  table.board { width: 100%; border-collapse: collapse; }
  table.board th { font-size: 13px; letter-spacing: 2px; color: #4a4238;
                   border-top: 1px solid #2b2620; border-bottom: 2px solid #2b2620;
                   padding: 5px 6px; text-align: left; }
  table.board td { padding: 6px 6px; border-bottom: 1px solid rgba(43,38,32,.28);
                   font-variant-numeric: tabular-nums; }
  .rk { font-weight: 900; font-size: 16px; width: 44px; }
  .rk.top { color: #a53326; }
  .b-score { font-size: 16px; font-weight: 800; }

  /* ---- 结算 ---- */
  .settle-entry { border: 1px solid #2b2620; margin-bottom: 10px;
                  background: rgba(255,252,240,.45); }
  .settle-entry.champ { border-width: 2px; box-shadow: 3px 3px 0 rgba(43,38,32,.25); }
  .se-h { display: flex; align-items: center; gap: 10px; padding: 7px 12px;
          border-bottom: 1px solid rgba(43,38,32,.4); }
  .se-rank { font-size: 20px; font-weight: 900; color: #a53326; }
  .se-name { font-size: 19px; font-weight: 800; letter-spacing: 1px; }
  .se-reason { font-size: 12px; color: #6b5f4d; border: 1px solid #6b5f4d;
               padding: 0 6px; border-radius: 2px; }
  .se-score { margin-left: auto; text-align: right; }
  .se-score b { font-size: 22px; font-variant-numeric: tabular-nums; }
  .se-score b.neg { color: #a53326; }
  .se-score small { display: block; font-size: 11px; color: #6b5f4d; letter-spacing: 2px; }
  .se-body { padding: 6px 12px 9px; font-size: 13.5px; }
  .se-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 4px; }
  .se-row .k { color: #6b5f4d; letter-spacing: 2px; }
  .chip-title { border: 1px solid #a53326; color: #a53326; font-weight: 700;
                padding: 0 8px; border-radius: 2px; font-size: 13px; }
  .chip-ach { background: #8a6d3b; color: #f6efdc; padding: 1px 8px;
              border-radius: 2px; font-size: 12.5px; }
  .se-board { margin-top: 5px; color: #a53326; font-weight: 700; }

  /* ---- 帮助 ---- */
  .cmd-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 20px; }
  .cmd { display: flex; gap: 8px; align-items: baseline; padding: 3px 0;
         border-bottom: 1px dashed rgba(43,38,32,.3); font-size: 13.5px; }
  .cmd code {
    font-family: "Sarasa Mono SC", "Noto Sans Mono CJK SC", monospace;
    background: rgba(43,38,32,.08); border: 1px solid rgba(43,38,32,.35);
    padding: 0 6px; border-radius: 2px; font-size: 12.5px; white-space: nowrap;
  }
  .cmd .d { color: #4a4238; }

  /* ---- 贴士 / 命令提示 ---- */
  .notes { border: 1px solid #a53326; background: rgba(165,51,38,.06);
           padding: 6px 12px; font-size: 13.5px; color: #7c2820; }
  .notes .n + .n { margin-top: 2px; }
  .notes .n::before { content: "※ "; font-weight: 900; }
  .hints { border: 1px dashed #2b2620; padding: 8px 12px; margin-top: 14px;
           background: rgba(255,252,240,.5); }
  .hints-h { font-size: 12.5px; letter-spacing: 3px; color: #6b5f4d; margin-bottom: 4px; }
  .hint-items { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12.5px; }
  .hint-items code {
    font-family: "Sarasa Mono SC", "Noto Sans Mono CJK SC", monospace;
    background: rgba(43,38,32,.08); border: 1px solid rgba(43,38,32,.35);
    padding: 0 5px; border-radius: 2px;
  }
  .hint-items .hd { color: #6b5f4d; }

  /* ---- 页脚与印章 ---- */
  .foot { position: relative; margin-top: 12px; border-top: 3px double #2b2620;
          padding: 5px 96px 0 0; min-height: 68px;
          display: flex; justify-content: space-between; align-items: flex-start;
          font-size: 11.5px; color: #6b5f4d; letter-spacing: 2px; }
  .seal {
    position: absolute; right: 2px; top: -10px; width: 74px; height: 74px;
    border: 3px solid rgba(165,51,38,.72); border-radius: 4px;
    display: grid; grid-template-columns: 1fr 1fr; place-items: center;
    color: rgba(165,51,38,.8); font-weight: 900; font-size: 24px; line-height: 1;
    transform: rotate(-8deg); padding: 4px;
  }
</style>
"""

# 印章四字简化为两列排布，盖在页脚右侧的预留空位上
_SEAL = """
<div class="seal"><span>浮</span><span>生</span><span>记</span><span>印</span></div>
"""

_HINTS = """
{% if hints or locmap %}
<div class="hints">
  {% if hints %}
  <div class="hints-h">◇ 常用命令（把「浮生记」二字带上）◇</div>
  <div class="hint-items">
    {% for h in hints %}<span><code>{{ h.cmd }}</code> <span class="hd">{{ h.desc }}</span></span>{% endfor %}
  </div>
  {% endif %}
  {% if locmap %}
  <div class="hints-h"{% if hints %} style="margin-top:7px"{% endif %}>◇ 京城十站（如「浮生记 去 3」）◇</div>
  <div class="hint-items">
    {% for l in locmap %}<span><code>{{ l.idx }}</code> <span class="hd">{{ l.name }}</span></span>{% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}
"""

_FOOT = (
    """
<div class="foot">
  <span>{{ foot_left }}</span>
  <span>本报谢绝转载 · 小道消息概不负责</span>
"""
    + _SEAL
    + """
</div>
"""
)


def _doc(masthead: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        + f"<meta name='viewport' content='width={VIEWPORT_WIDTH}, height={VIEWPORT_HEIGHT}'>"
        + _CSS
        + "</head><body><div class='paper'><div class='frame'>"
        + masthead
        + body
        + _HINTS
        + _FOOT
        + "</div></div></body></html>"
    )


_MASTHEAD_DAILY = """
<div class="masthead">
  <div class="mh-kicker">{{ kicker }}</div>
  <div class="mh-title">京城<span class="mh-mark">浮生</span>小报</div>
  <div class="mh-sub">{{ sub }}</div>
  <div class="mh-rule"></div>
  <div class="mh-slogan"><span>{{ slogan_left }}</span><span>{{ slogan_right }}</span></div>
</div>
"""

# ---------------------------------------------------------------------------
# 组件片段
# ---------------------------------------------------------------------------

_SEC_HEADLINES = """
<div class="sec">
  <div class="sec-h"><span class="tag red">头版快讯</span><span class="line"></span></div>
  <div class="headlines">
    {% if headlines %}
      {% for h in headlines %}
      <div class="hl{% if loop.first %} first{% endif %}"><span class="dot">{% if loop.first %}●{% else %}○{% endif %}</span><span>{{ h }}</span></div>
      {% endfor %}
    {% else %}
      <div class="hl calm"><span class="dot">○</span><span>今日街面风平浪静，各路行情按部就班。</span></div>
    {% endif %}
  </div>
</div>
{% if notes %}
<div class="sec"><div class="notes">{% for n in notes %}<div class="n">{{ n }}</div>{% endfor %}</div></div>
{% endif %}
"""

_SEC_EVENTS = """
{% if events %}
<div class="sec">
  <div class="sec-h"><span class="tag">街头见闻</span><span class="line"></span></div>
  {% for e in events %}
  <div class="ev-p">
    <div class="ev-name">{{ e.name }}</div>
    {% for li in e.lines %}<div class="ev-li">{{ li }}</div>{% endfor %}
  </div>
  {% endfor %}
</div>
{% endif %}
"""

_SEC_MARKET = """
<div class="sec">
  <div class="sec-h"><span class="tag">今日行情</span><span class="line"></span><span class="aside">{{ market_aside }}</span></div>
  <table class="mkt">
    <tr><th>序</th><th>货品</th><th style="text-align:right">时价</th><th style="padding-left:14px">异动</th>{% if show_mine %}<th style="text-align:right">你的持仓</th>{% endif %}</tr>
    {% for g in market %}
    <tr{% if g.off %} class="off"{% endif %}>
      <td class="g-idx">{{ g.idx }}</td>
      <td class="g-name">{{ g.name }}{% if g.fame %} <small>缺德货</small>{% endif %}</td>
      <td class="g-price">{% if g.off %}无行情{% else %}{{ g.price }}<small> 元</small>{% endif %}</td>
      <td class="g-flag" style="padding-left:14px">
        {%- if g.ev == "up" %}<span class="b b-up">暴涨</span>{% elif g.ev == "down" %}<span class="b b-down">暴跌</span>{% endif -%}
        {%- if g.imp == "up" %}<span class="b b-imp-up">▲抬价{{ g.imp_pct }}</span>{% elif g.imp == "down" %}<span class="b b-imp-down">▼砸价{{ g.imp_pct }}</span>{% endif -%}
      </td>
      {% if show_mine %}<td class="g-mine">{% if g.mine %}{{ g.mine }}{% else %}·{% endif %}</td>{% endif %}
    </tr>
    {% endfor %}
  </table>
</div>
"""

_PLAYER_CARDS = """
<div class="sec">
  <div class="sec-h"><span class="tag">{{ cards_title }}</span><span class="line"></span><span class="aside">{{ cards_aside }}</span></div>
  <div class="cards{% if cards|length == 1 %} single{% endif %}">
    {% for c in cards %}
    <div class="card{% if c.dim %} dim{% endif %}">
      <div class="card-h">
        <div class="ch-top">
          <span class="rank-medal{% if c.dead %} dead{% endif %}">{{ c.medal }}</span>
          <span class="p-name">{{ c.name }}</span>
          <span class="p-nw"><b>{{ c.nw }}</b><small>身家（元）</small></span>
        </div>
        <div class="ch-sub">
          <span class="p-loc">{{ c.loc }}</span>
          {% if c.tag %}<span class="p-tag">{{ c.tag }}</span>{% endif %}
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat"><span class="k">现金</span><span class="v">{{ c.cash }}</span></div>
        <div class="stat"><span class="k">存款</span><span class="v">{{ c.bank }}</span></div>
        <div class="stat"><span class="k">债务</span><span class="v{% if c.debt_hot %} neg{% endif %}">{{ c.debt }}</span></div>
        <div class="stat"><span class="k">仓库</span><span class="v">{{ c.cap }}</span></div>
      </div>
      <div class="barline"><span class="k">健康</span><div class="bar"><i class="{{ c.hp_cls }}" style="width:{{ c.hp_pct }}%"></i></div><span class="v">{{ c.hp }}/100</span></div>
      <div class="barline"><span class="k">名声</span><div class="bar"><i class="fame" style="width:{{ c.fame_pct }}%"></i></div><span class="v">{{ c.fame }}·{{ c.fame_title }}</span></div>
      <div class="inv">
        {% if c.inv %}{% for it in c.inv %}<span class="inv-chip">{{ it.name }}<b>×{{ it.qty }}</b></span>{% endfor %}
        {% elif not c.dim %}<span class="inv-empty">（两手空空，屋里连个耗子都不来）</span>{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>
"""

# ---------------------------------------------------------------------------
# 完整版面
# ---------------------------------------------------------------------------

TMPL_DAY = _doc(
    _MASTHEAD_DAILY,
    _SEC_HEADLINES + _SEC_EVENTS + _SEC_MARKET + _PLAYER_CARDS,
)

_SEC_TIP = """
{% if tip %}
<div class="sec"><div class="notes"><div class="n">{{ tip }}</div></div></div>
{% endif %}
"""

_SEC_MY_INV = """
<div class="sec">
  <div class="sec-h"><span class="tag">存货账本</span><span class="line"></span><span class="aside">{{ inv_aside }}</span></div>
  {% if inv_rows %}
  <table class="inv-t">
    <tr><th>货品</th><th style="text-align:right">数量</th><th style="text-align:right">进价均价</th><th style="text-align:right">时价</th><th style="text-align:right">账面盈亏</th></tr>
    {% for r in inv_rows %}
    <tr>
      <td>{{ r.name }}</td>
      <td style="text-align:right">{{ r.qty }}</td>
      <td style="text-align:right">{{ r.avg }}</td>
      <td style="text-align:right">{{ r.cur }}</td>
      <td style="text-align:right" class="{{ r.pnl_cls }}">{{ r.pnl }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="inv-empty">屋里空空如也。低买高卖，才是浮生正道。</div>
  {% endif %}
</div>
"""

TMPL_PANEL = _doc(
    _MASTHEAD_DAILY,
    _SEC_TIP + _PLAYER_CARDS + _SEC_MY_INV + _SEC_MARKET + _SEC_HEADLINES,
)

_SEC_SETTLE = """
<div class="sec">
  <div class="sec-h"><span class="tag red">最终结算</span><span class="line"></span><span class="aside">{{ settle_aside }}</span></div>
  {% for e in entries %}
  <div class="settle-entry{% if loop.first and e.champ %} champ{% endif %}">
    <div class="se-h">
      <span class="se-rank">{{ e.rank }}</span>
      <span class="se-name">{{ e.name }}</span>
      <span class="se-reason">{{ e.reason }}</span>
      <span class="se-score">{% if e.score %}<b{% if e.neg %} class="neg"{% endif %}>{{ e.score }}</b><small>结算身家（元）</small>{% else %}<b class="neg">{{ e.score_alt }}</b>{% endif %}</span>
    </div>
    <div class="se-body">
      <div class="se-row">
        {% if e.title %}<span class="chip-title">{{ e.title }}</span>{% endif %}
        <span class="k">名声</span><span>{{ e.fame_title }}（{{ e.fame }}）</span>
      </div>
      {% if e.achievements %}
      <div class="se-row"><span class="k">成就</span>{% for a in e.achievements %}<span class="chip-ach">{{ a }}</span>{% endfor %}</div>
      {% endif %}
      {% if e.board %}<div class="se-board">{{ e.board }}</div>{% endif %}
      {% if e.epitaph %}<div class="se-row"><span class="k">{{ e.epitaph }}</span></div>{% endif %}
    </div>
  </div>
  {% endfor %}
</div>
"""

TMPL_SETTLE = _doc(_MASTHEAD_DAILY, _SEC_SETTLE)

_SEC_BOARD = """
<div class="sec">
  <div class="sec-h"><span class="tag red">浮生龙虎榜</span><span class="line"></span><span class="aside">历史十强 · 只认身家不认人</span></div>
  <table class="board">
    <tr><th>名次</th><th>姓名</th><th style="text-align:right">身家（元）</th><th>称号</th><th>名声</th><th style="text-align:right">天数</th><th>上榜日</th></tr>
    {% for r in rows %}
    <tr>
      <td class="rk{% if loop.index <= 3 %} top{% endif %}">{{ r.rank }}</td>
      <td style="font-weight:700">{{ r.name }}</td>
      <td style="text-align:right" class="b-score">{{ r.score }}</td>
      <td>{{ r.title }}</td>
      <td>{{ r.fame_title }}</td>
      <td style="text-align:right">{{ r.days }}</td>
      <td>{{ r.date }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
"""

TMPL_BOARD = _doc(_MASTHEAD_DAILY, _SEC_BOARD)

_SEC_HELP = """
<div class="sec">
  <div class="sec-h"><span class="tag red">开局须知</span><span class="line"></span></div>
  <div class="headlines">
    {% for h in intro %}<div class="hl"><span class="dot">●</span><span>{{ h }}</span></div>{% endfor %}
  </div>
</div>
{% for grp in groups %}
<div class="sec">
  <div class="sec-h"><span class="tag">{{ grp.title }}</span><span class="line"></span>{% if grp.aside %}<span class="aside">{{ grp.aside }}</span>{% endif %}</div>
  <div class="cmd-grid">
    {% for c in grp.cmds %}<div class="cmd"><code>{{ c.cmd }}</code><span class="d">{{ c.desc }}</span></div>{% endfor %}
  </div>
</div>
{% endfor %}
<div class="sec">
  <div class="sec-h"><span class="tag">京城十站</span><span class="line"></span><span class="aside">{{ locs_aside }}</span></div>
  <div class="hint-items" style="font-size:13.5px">
    {% for l in locs %}<span><code>{{ l.idx }}</code> <span class="hd">{{ l.name }}</span></span>{% endfor %}
  </div>
</div>
<div class="sec">
  <div class="sec-h"><span class="tag">货品一览</span><span class="line"></span><span class="aside">时价每天重开，低买高卖</span></div>
  <table class="mkt">
    <tr><th>序</th><th>货品</th><th style="text-align:right">常见价位</th><th style="padding-left:14px">备注</th></tr>
    {% for g in goods %}
    <tr>
      <td class="g-idx">{{ g.idx }}</td>
      <td class="g-name">{{ g.name }}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">{{ g.range }}</td>
      <td style="padding-left:14px;font-size:13px;color:#4a4238">{{ g.note }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
"""

TMPL_HELP = _doc(_MASTHEAD_DAILY, _SEC_HELP)
