"""游戏数值表与文案。

数值机制忠实还原《北京浮生记》v1.2.2 原版源码（随机判定写法、概率、
公式与顺序均一致，详见 README「与原版的差异」一节）；全部叙事文案为
本插件原创重写。

随机数约定（与原版一致）：
    rand(N)  = rng.randrange(N)，取 [0, N-1]
    事件判定 = rand(POOL) % freq == 0
所有金额、价格均为整数（元），除法一律向下取整。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 基础常量
# ---------------------------------------------------------------------------

START_CASH = 2000
START_DEBT = 5000  # 开局立即结息一次 -> 玩家第 1 天看到 5500
START_HEALTH = 100
START_FAME = 100
MAX_HEALTH = 100

DEFAULT_DAYS = 40
MIN_DAYS, MAX_DAYS = 5, 99

START_CAPACITY = 100
MAX_CAPACITY = 140
HOUSE_STEP = 10  # 每次租房容量 +10

DEBT_RATE_PCT = 10  # 债务日息 10%（向下取整）
BANK_RATE_PCT = 1  # 存款日息 1%（向下取整）

HOSPITAL_PRICE_PER_POINT = 3500  # 医院每点健康 3500 元

HOUSE_MIN_CASH = 30000  # 租房中介的门槛
# 中介实扣与报价不符是原版刻意设定的"黑中介"玩法：
#   cash <= 30000 -> 实扣 25000（嘴上说只收两万）
#   cash >  30000 -> cash = cash // 2 - 2000（嘴上说只收一半）
HOUSE_FLAT_COST = 25000

CAFE_MIN_CASH = 15  # 网吧门槛
CAFE_MAX_TIMES = 3  # 每局每人最多打工 3 次
CAFE_REWARD = (1, 10)  # 广告费 1 + rand(10) -> [1, 10]

THUG_DEBT_LINE = 100000  # 债务超过此数，每天被打手揍
THUG_DAMAGE = 30

HOSPITAL_FORCE_HEALTH = 85  # 健康低于 85 且剩余天数 > 3 -> 强制住院
HOSPITAL_FORCE_MIN_DAYS_LEFT = 3
HEALTH_WARN_LINE = 20  # 0 < 健康 < 20 时警告

BUSINESS_POOL = 950  # 商业事件判定池 rand(950) % freq
MISC_POOL = 1000  # 健康/亏钱事件判定池 rand(1000) % freq

HACKER_MOD = 25  # rand(1000) % 25 == 0 -> 黑客光顾（4%/天）

FULL_MARKET_LAST_DAYS = 2  # 最后 2 个交易日全部商品上架
DELIST_ROLLS = 3  # 平日有放回抽 3 次下架

# 每日强制住院：天数 1 + rand(2)，账单 天数 * (1000 + rand(8500)) 记入债务
HOSPITAL_STAY_RAND = 2
HOSPITAL_BILL_BASE = 1000
HOSPITAL_BILL_RAND = 8500
HOSPITAL_STAY_HEAL = 10  # 住院回 10 点健康

MAX_PLAYERS = 4

# ---- 创新玩法 ----

IMPACT_UNIT = 25000  # 单商品当日净买入/卖出每满 25000 元，价格 ±5%
IMPACT_STEP_PCT = 5
IMPACT_MAX_STEPS = 6  # 封顶 ±30%

INTEL_DEFAULT_PRICE = 500  # 网吧买情报的默认价
INTEL_DEFAULT_ACCURACY = 75  # 情报默认准确率（百分比）

LEADERBOARD_SIZE = 10

# ---------------------------------------------------------------------------
# 地点（纯地图皮肤，不影响价格；与原版地铁图一致）
# ---------------------------------------------------------------------------

LOCATIONS: list[str] = [
    "北京站",
    "建国门",
    "西直门",
    "崇文门",
    "东直门",
    "复兴门",
    "积水潭",
    "长椿街",
    "公主坟",
    "苹果园",
]
START_LOCATION = 0  # 北京站

# ---------------------------------------------------------------------------
# 商品
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Good:
    name: str  # 面板显示名
    short: str  # 简称（命令里可用）
    base: int  # 价格 = base + rand(rand_range)
    rand_range: int
    fame_cost: int = 0  # 每卖出一笔扣的名声
    aliases: tuple[str, ...] = ()


GOODS: list[Good] = [
    Good("进口香烟", "香烟", 100, 350, aliases=("烟", "yan")),
    Good("走私汽车", "汽车", 15000, 15000, aliases=("车", "che")),
    Good("盗版VCD·游戏", "盗版VCD", 5, 50, aliases=("vcd", "VCD", "光盘", "游戏")),
    Good("假白酒(剧毒!)", "假白酒", 1000, 2500, fame_cost=10, aliases=("酒", "白酒", "假酒", "jiu")),
    Good("假古董字画", "假古董", 5000, 9000, fame_cost=7, aliases=("古董", "字画", "gudong")),
    Good("进口玩具", "玩具", 250, 600, aliases=("wanju",)),
    Good("水货手机", "手机", 750, 750, aliases=("shouji",)),
    Good("伪劣化妆品", "化妆品", 65, 180, aliases=("hzp", "假化妆品")),
]

N_GOODS = len(GOODS)

# ---------------------------------------------------------------------------
# 商业事件
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceEvent:
    """价格暴涨/暴跌（房间级：每天判定一次，全体玩家共享行情）。"""

    freq: int  # rand(950) % freq == 0
    good: int  # 商品下标
    mul: int = 0  # 价格 x mul（涨）
    div: int = 0  # 价格 // div（跌）
    text: str = ""

    @property
    def is_up(self) -> bool:
        return self.mul > 0


# 顺序与原版一致（0..13），判定逐条独立、互不 break。
PRICE_EVENTS: list[PriceEvent] = [
    PriceEvent(170, 5, mul=2, text="教育专家痛心疾首：现在的大学生连玩具都不会拼！家长闻风抢购益智玩具。"),
    PriceEvent(139, 3, mul=3, text="胡同里疯传偏方：假白酒兑水喝能强身健体。愣是被炒成了'神仙水'。"),
    PriceEvent(100, 4, mul=5, text="鉴宝节目一炮而红，人人自称慧眼识珠，潘家园的假古董字画身价倍增。"),
    PriceEvent(41, 2, mul=4, text="大爷在报刊亭放话：得诺贝尔奖有啥用，不如两张盗版碟实惠！全城疯抢。"),
    PriceEvent(37, 1, mul=3, text="小报社论高呼'超前消费光荣'，走私汽车成了款爷们的硬通货。"),
    PriceEvent(23, 7, mul=4, text="晚报整版鼓吹'爱美要落到实处'，化妆品柜台被挤破了玻璃。"),
    PriceEvent(37, 4, mul=8, text="琉璃厂放出风声：某'祖传真迹'拍出天价！假古董字画在黑市一画难求。"),
    PriceEvent(15, 7, mul=7, text="当红歌星在晚会上喊出'我酷！我用的就是这牌子！'——伪劣化妆品卖疯了。"),
    PriceEvent(40, 3, mul=7, text="外地酒厂集体出事停产，京城酒桌告急，连假白酒都成了抢手货。"),
    PriceEvent(29, 6, mul=7, text="毕业季找工作，人手一部手机才有面子，水货手机行情看涨。"),
    PriceEvent(35, 1, mul=8, text="款爷圈流行'开走私车才叫低调的奢华'，车贩子坐地起价。"),
    PriceEvent(17, 0, div=8, text="南边整船的走私香烟涌进京城，烟价当场崩盘。"),
    PriceEvent(24, 5, div=5, text="小孩全泡在网吧里，谁还玩玩具？进口玩具堆成山没人要。"),
    PriceEvent(18, 2, div=8, text="中关村人手一摞盗版碟沿街兜售，五块钱三张还送袋子，价格烂穿地板。"),
]


@dataclass(frozen=True)
class GiftEvent:
    """白捡商品事件（玩家级：每人每天独立判定，顺序在价格事件之后）。"""

    freq: int
    good: int
    qty: int
    add_debt: int = 0
    text: str = ""  # {qty} 为实际入库数量占位


GIFT_EVENTS: list[GiftEvent] = [
    GiftEvent(160, 1, 2, text="厦门的老同学发达了，拍着你的肩膀塞来 {qty} 辆走私汽车：'兄弟，拿去发财！'"),
    GiftEvent(45, 0, 6, text="工商大盖帽刚扫荡完，老乡跑路时丢下 {qty} 条进口香烟，全便宜了你。"),
    GiftEvent(35, 3, 4, text="老乡回乡前把 {qty} 瓶假白酒硬塞给你：'城里人就好这口，卖了别念着我。'"),
    GiftEvent(
        140, 6, 1, add_debt=2500,
        text="村长'关照'你：一部三无水货手机硬塞到手里，账上给你记了 2500 元，利滚利。",
    ),
]

GIFT_HOUSE_FULL_TEXT = "屋里堆得下不去脚，白给的货都没地方塞——真是穷人乍富，家徒四壁还嫌小。"

# ---------------------------------------------------------------------------
# 健康事件（玩家级：每天按序判定，命中一条即止）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthEvent:
    freq: int
    damage: int
    text: str


HEALTH_EVENTS: list[HealthEvent] = [
    HealthEvent(117, 3, "两个街头混混拦住你比划半天，临走一人给了你一拳'留念'。"),
    HealthEvent(157, 20, "过街地道里冷不丁挨了一闷棍，醒来时兜里的东西倒是没丢，人是懵的。"),
    HealthEvent(21, 1, "大盖帽在后面追了你三条胡同，跑得肺都要炸了。"),
    HealthEvent(100, 1, "堵在二环上一动不动，尾气熏得你头晕眼花，心口发闷。"),
    HealthEvent(35, 1, "小巴司机嫌你问路耽误拉客，一巴掌把你扇下了车。"),
    HealthEvent(313, 10, "在工地边看热闹，被一群壮汉当成闹事的围起来推搡了一顿。"),
    HealthEvent(120, 5, "路边几个半大小子起哄，一块砖头不偏不倚砸在你背上。"),
    HealthEvent(29, 3, "'保安'查你暂住证，电棍先招呼上了，查完才说认错人了。"),
    HealthEvent(43, 1, "路过一条泛着泡的黑水河，一股怪味直冲天灵盖，差点背过气去。"),
    HealthEvent(45, 1, "看车的大婶当街数落你'没户口还倒腾买卖'，围观群众哈哈大笑，你气得胸口疼。"),
    HealthEvent(48, 1, "四十度高温，柏油路都晒软了，你中暑了。"),
    HealthEvent(33, 1, "沙尘暴说来就来，一嘴沙子一身土，嗓子眼火辣辣的。"),
]

# ---------------------------------------------------------------------------
# 亏钱事件（玩家级：每天按序判定，命中一条即止）
# 公式（原版原样）：new = (x // 100) * (100 - pct)，亏损 = x - new
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoneyEvent:
    freq: int
    target: str  # "cash" | "bank"
    pct: int
    text: str  # {amount} 为实际损失金额占位


MONEY_EVENTS: list[MoneyEvent] = [
    MoneyEvent(60, "cash", 10, "地铁口的'可怜'老太太拉着你哭诉了半个钟头，你心一软掏了 {amount} 元。"),
    MoneyEvent(125, "cash", 10, "一条硬汉子往你跟前一站：'兄弟，借点钱花花。'你乖乖交出 {amount} 元。"),
    MoneyEvent(100, "cash", 40, "公交车上被大个子'不小心'撞了一下，回过神来兜里少了 {amount} 元。"),
    MoneyEvent(65, "cash", 20, "戴红袖章的大妈咬定你占道经营，'罚款' {amount} 元，还不给条子。"),
    MoneyEvent(35, "bank", 15, "'电信局的猛男'上门收'长话附加费'，从你存款里划走 {amount} 元。"),
    MoneyEvent(27, "bank", 10, "办证窗口的副主任暗示'手续费另算'，你存款少了 {amount} 元。"),
    MoneyEvent(40, "cash", 5, "空气污染太狠，你嗓子冒烟，进氧吧吸了口新鲜空气，花了 {amount} 元。"),
]

# ---------------------------------------------------------------------------
# 黑客事件（玩家级；原版为可选项，本插件默认开启，可在配置中关闭）
#   rand(1000) % 25 == 0 触发：
#     bank < 1000                无事
#     1000 <= bank <= 100000     bank += bank // (1 + rand(15))   只赚不赔
#     bank > 100000              num = bank // (2 + rand(20))
#                                rand(20) % 3 != 0 -> 扣 num；否则加 num
# ---------------------------------------------------------------------------

HACKER_SMALL_LINE = 1000
HACKER_BIG_LINE = 100000
HACKER_GAIN_TEXT = "神秘黑客劫富济贫，往你的银行账户里塞了 {amount} 元，留言：'拿好，别声张。'"
HACKER_LOSS_TEXT = "黑客黑进了银行网络，你的存款被划走 {amount} 元，柜员两手一摊：'系统问题。'"

THUG_TEXT = "村长派来的两个大汉把你堵在墙角：'欠了十万还想跑？'一顿胖揍（健康 -30）。"

HOSPITAL_FORCE_TEXT = (
    "你脸色蜡黄走路打晃，被巡逻的好心人直接架进了医院。强制住院 {days} 天，"
    "账单 {bill} 元先记在债上（健康 +10）。"
)
HOSPITAL_DISCHARGE_TEXT = "医生在你的出院单上敲了章：'下回再这么糟践身体，可就不是住几天的事了。'"
HEALTH_WARN_TEXT = "⚠️ 你已经虚弱得扶墙走路了，再不去医院，怕是要交代在北京街头。"
DEATH_TEXT = "你眼前一黑倒在街头，再也没有醒来。京城依旧车水马龙，没人记得你来过。"

# ---------------------------------------------------------------------------
# 设施文案
# ---------------------------------------------------------------------------

HOUSE_AGENT_QUOTE_FLAT = "中介小哥拍胸脯：'哥们儿，两万块，屋子给你扩出十平米！'"
HOUSE_AGENT_QUOTE_HALF = "中介小哥眯眼一笑：'您这身家，收您现金的一半，童叟无欺！'"
HOUSE_AGENT_DONE = "搬家完毕，仓库容量 +10（现 {cap}）。事后一数钱……实扣 {real} 元。黑，真黑。"

CAFE_TEXTS = [
    "你在网吧帮网管贴了一下午小广告，挣了 {amount} 元，还蹭了杯免费茶水。",
    "你替包夜的哥们儿代练了俩钟头，对方扔下 {amount} 元：'就这水平？'",
    "你在网吧门口吆喝'一小时两块'，拉来仨客人，网管分你 {amount} 元。",
]

INTEL_TEXTS_INTRO = "网吧角落里的'消息灵通人士'凑过来压低嗓门："
INTEL_UP_TEXT = "'明儿个 {good} 有大动静，指定暴涨，信我！'"
INTEL_DOWN_TEXT = "'明儿个 {good} 要栽大跟头，手里有货趁早出！'"
INTEL_NO_TOMORROW = "明天就要收摊回家了，还打听什么行情？"
INTEL_ALREADY = "今天已经打听过了，消息贩子摆摆手：'明儿再来。'"

# 还清债务后按总资产的四档嘲讽（原版彩蛋的原创重写）
DEBT_CLEAR_TIERS: list[tuple[int, str]] = [
    (1000, "村长收了钱撇撇嘴：'就这点出息，也好意思在北京混？'"),
    (100000, "村长掂了掂钱：'行啊小子，比村口二狗强点了。'"),
    (10000000, "村长搓着手陪笑：'早看出您是干大事的人！回村给您立块碑？'"),
    (1 << 62, "村长扑通一声跪下了：'财神爷！村里的路，就指望您了！'"),
]

# ---------------------------------------------------------------------------
# 结算文案
# ---------------------------------------------------------------------------

# 上榜随机称号（rand % 5，原版机制、原创文案）
SCORE_TITLES = [
    "京城倒爷之光",
    "二环内最强操盘手",
    "黑市传奇",
    "浮生捞金圣手",
    "天桥底下商业奇才",
]

# 名声称号（阈值同原版；原版 10~19 档因源码笔误不可达，本插件已修复）
FAME_TITLES: list[tuple[int, str]] = [
    (100, "德艺双馨"),
    (90, "有口皆碑"),
    (80, "中规中矩"),
    (60, "毁誉参半"),
    (40, "风评被害"),
    (20, "臭名远扬"),
    (10, "过街老鼠"),
    (0, "千夫所指"),
]

ENDING_BROKE_TEXT = "四十天折腾下来分文不剩，你被遣送回了老家。村口的大喇叭广播了整整三天。"
ENDING_DEAD_RANK_TEXT = "客死他乡"
ENDING_SURRENDER_TEXT = "你收拾行李提前离场：'这京城的水，太深了。'"

# 默认榜首（向原版致敬的种子数据）
SEED_CHAMPION = {"name": "赖皮张", "score": 12500720}

# 成就（结算时依据行为统计颁发）
ACHIEVEMENTS: list[tuple[str, str, str]] = [
    # (key, 名称, 描述)
    ("millionaire", "百万富翁", "结算净资产 ≥ 100 万"),
    ("tycoon", "千万富豪", "结算净资产 ≥ 1000 万"),
    ("debt_free", "无债一身轻", "还清过全部债务"),
    ("windfall", "一夜暴富", "单日净资产增长 ≥ 10 万"),
    ("trader", "倒爷祖师", "累计成交 ≥ 60 笔"),
    ("scrooge", "空手套白狼", "卖白捡的货净赚 ≥ 5 万"),
    ("survivor", "九死一生", "健康一度 ≤ 10 仍活到最后"),
    ("clean_hands", "干净买卖", "全程未卖过假白酒和假古董"),
    ("net_addict", "网吧常客", "网吧打工满 3 次"),
    ("informed", "包打听", "累计购买情报 ≥ 5 次"),
]

WINDFALL_LINE = 100000
TRADER_LINE = 60
SCROOGE_LINE = 50000
SURVIVOR_LINE = 10
INFORMED_LINE = 5
