"""AstrBot 插件入口：命令路由、会话锁、持久化与图片渲染分发。

分层约定：
    main.py   —— 平台适配层（本文件）：把命令翻译成引擎调用，把结果翻译成消息
    core/     —— 纯游戏逻辑：引擎、行情、输入解析、存档（不依赖 AstrBot）
    render/   —— 版面：HTML 模板 + 上下文构建 + 纯文本兜底

并发模型：以 unified_msg_origin 为房间键，每个房间一把 asyncio.Lock；
引擎调用与存盘都在锁内完成，图片渲染（网络请求）在锁外进行。
"""

from __future__ import annotations

import asyncio
import difflib
import random
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import const, engine, parse
from .core.errors import GameError
from .core.models import PHASE_RUNNING, PHASE_SIGNUP, Room
from .core.store import GameStore
from .render import RENDER_OPTIONS, contexts, templates, text_fallback

PLUGIN_NAME = "astrbot_plugin_beijing_fushengji"

_NO_GAME_TEXT = (
    "本群还没有开局。\n"
    "💡 发送「浮生记 创建」开一局，「浮生记 帮助」看玩法说明书。"
)

AI_TRIES = 3  # 说书人首选供应商的累计尝试次数（含第一次）
AI_TIMEOUT = 30  # 单次生成的超时（秒）

# 唤醒词与子命令注册表。框架对「唤醒词对、子命令错」的消息不报错，而是当普通
# 对话丢给大模型接茬，模型会一本正经地编造"交易成功"——guard_typo 靠这张表把
# 这类消息拦下。表必须与下方 @fusheng.command 的注册一一对应（测试会核对）。
WAKE_WORDS = ("浮生记", "fs", "浮生")
SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "创建": ("开局", "new"),
    "加入": ("上车", "join"),
    "开始": ("发车", "start"),
    "去": ("前往", "赶路", "go"),
    "留守": ("原地", "休整", "stay"),
    "买": ("购买", "进货", "buy"),
    "卖": ("出售", "出货", "sell"),
    "存": ("存款", "存钱"),
    "取": ("取款", "取钱"),
    "还": ("还债", "还钱"),
    "看病": ("治疗", "医院"),
    "租房": ("扩容", "搬家"),
    "网吧": ("打工", "上网"),
    "情报": ("消息", "小道消息"),
    "面板": ("状态", "账本", "我"),
    "排行": ("座次", "战况"),
    "榜单": ("龙虎榜", "排行榜"),
    "帮助": ("说明", "玩法", "help"),
    "跳过": ("催", "催场"),
    "认输": ("投降", "跑路"),
    "解散": ("散伙",),
}
# 任一写法（本名或别名）-> 本名
_SUB_TO_CANON: dict[str, str] = {
    form: canon
    for canon, aliases in SUBCOMMANDS.items()
    for form in (canon, *aliases)
}

# 输出指令：("text", str) / ("image", tmpl, ctx, fallback_fn) / ("epilogue", settlement)
_Out = tuple[Any, ...]


class BeijingFushengji(Star):
    """群聊多人版《北京浮生记》。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.conf: dict[str, Any] = config if config is not None else {}
        self.store = GameStore(StarTools.get_data_dir(PLUGIN_NAME))
        self.rng = random.Random()
        self.rooms: dict[str, Room] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.ai_retry_delay: float = 1.5  # AI 点评重试间隔（秒），测试置 0
        self._soften_bare_wake_word()

    async def initialize(self) -> None:
        logger.info(f"[浮生记] 插件已加载，数据目录：{self.store.base_dir}")

    async def terminate(self) -> None:
        # 每次变更都已即时落盘，这里无需额外收尾
        logger.info("[浮生记] 插件已卸载。")

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.conf.get(key, default)
        except Exception:
            return default

    def _lock(self, rid: str) -> asyncio.Lock:
        return self.locks.setdefault(rid, asyncio.Lock())

    def _load(self, rid: str) -> Room | None:
        room = self.rooms.get(rid)
        if room is None:
            data = self.store.load_room(rid)
            if data is not None:
                try:
                    room = Room.from_dict(data)
                except (KeyError, TypeError, ValueError):
                    logger.warning(f"[浮生记] 存档结构异常，已弃用：{rid}")
                    self.store.delete_room(rid)
                    return None
                self.rooms[rid] = room
        return room

    def _save(self, rid: str, room: Room) -> None:
        self.rooms[rid] = room
        self.store.save_room(rid, room.to_dict())

    def _drop(self, rid: str) -> None:
        self.rooms.pop(rid, None)
        self.store.delete_room(rid)

    @staticmethod
    def _room_label(rid: str) -> str:
        """把会话 ID 折叠成榜单上的来源标记（不泄露完整会话号）。"""
        seg = rid.rsplit(":", 1)[-1].strip()
        return f"群{seg[-4:]}" if seg else "-"

    def _sender(self, event: AstrMessageEvent) -> tuple[str, str]:
        uid = event.get_sender_id()
        name = (event.get_sender_name() or "").strip() or f"浮生客{str(uid)[-4:]}"
        return str(uid), name[:20]

    # ------------------------------------------------------------------
    # 结果 -> 消息
    # ------------------------------------------------------------------

    def _apply(self, rid: str, room: Room, result: engine.ActionResult) -> list[_Out]:
        """落盘并把 ActionResult 翻译成输出指令（在房间锁内调用）。"""
        outs: list[_Out] = []
        if result.lines:
            outs.append(("text", "\n".join(result.lines)))
        if result.day_reports:
            ctx = contexts.day_context(room, result.day_reports)
            outs.append(("image", templates.TMPL_DAY, ctx, text_fallback.day_text))
        if result.settlement is not None:
            outs += self._finalize(rid, result.settlement)
        else:
            self._save(rid, room)
        return outs

    def _finalize(self, rid: str, settlement: engine.Settlement) -> list[_Out]:
        """对局结束：并入历史榜（回填名次）、删档、产出结算图。"""
        board = self.store.load_leaderboard()
        board, changed = engine.merge_leaderboard(
            board, settlement, self._room_label(rid), time.time()
        )
        if changed:
            self.store.save_leaderboard(board)
        self._drop(rid)
        outs: list[_Out] = [
            ("image", templates.TMPL_SETTLE, contexts.settle_context(settlement),
             text_fallback.settle_text)
        ]
        if self._cfg("ai_comment", False):
            outs.append(("epilogue", settlement))
        return outs

    async def _emit(self, event: AstrMessageEvent, outs: list[_Out]) -> AsyncIterator:
        for out in outs:
            if out[0] == "text":
                yield event.plain_result(out[1])
            elif out[0] == "image":
                _, tmpl, ctx, fallback = out
                async for r in self._picture(event, tmpl, ctx, fallback):
                    yield r
            elif out[0] == "epilogue":
                text = await self._ai_epilogue(event, out[1])
                if text:
                    yield event.plain_result(text)

    async def _picture(
        self, event: AstrMessageEvent, tmpl: str, ctx: dict, fallback: Callable[[dict], str]
    ) -> AsyncIterator:
        """渲染版面图；服务不可用或被禁用时降级为同源文字。"""
        if not self._cfg("force_text_mode", False):
            try:
                path = await self.html_render(
                    tmpl, ctx, return_url=False, options=dict(RENDER_OPTIONS)
                )
                if path:
                    yield event.image_result(path)
                    return
                raise RuntimeError("渲染服务返回空结果")
            except Exception as e:
                logger.warning(f"[浮生记] 图片渲染失败，降级为文字：{e}")
        yield event.plain_result(fallback(ctx))

    def _resolve_ai_providers(self, umo: str) -> list[tuple[Any, int]]:
        """说书人的供应商梯队：[(供应商, 尝试次数)]。

        配置里指定了 ai_provider_id 且可用 -> 它试 AI_TRIES 次，会话默认模型兜底 1 次；
        未指定/指定的不可用 -> 会话默认模型就是首选，试 AI_TRIES 次。
        """
        pid = str(self._cfg("ai_provider_id", "") or "").strip()
        primary = None
        if pid:
            try:
                cand = self.context.get_provider_by_id(pid)
            except Exception:
                cand = None
            if cand is not None and hasattr(cand, "text_chat"):
                primary = cand
            else:
                logger.warning(f"[浮生记] 配置的说书人供应商「{pid}」不可用，改用当前会话模型。")
        try:
            fallback = self.context.get_using_provider(umo)
        except Exception as e:
            logger.warning(f"[浮生记] 获取会话默认模型失败：{e}")
            fallback = None
        plan: list[tuple[Any, int]] = []
        if primary is not None:
            plan.append((primary, AI_TRIES))
        if fallback is not None and fallback is not primary:
            plan.append((fallback, 1 if plan else AI_TRIES))
        return plan

    @staticmethod
    def _epilogue_prompts(s: engine.Settlement) -> tuple[str, str]:
        """组装说书人的 (对局数据, 系统提示词)。

        单人局与多人局各一套系统提示词；把数值规则与本局景气档位一并交给
        模型，并要求按「对照行情评价」的标准说话——冷清局赚小钱可夸、
        旺局只赚零头要点破、欠债与身故垫底但留口德，与结算页评语同一标准。
        """
        solo = len(s.entries) == 1
        room_line = (
            f"本局设定 {s.days_total} 天，实际进行 {s.days_played} 天，"
            f"行情档位「{s.boom_label}」：{s.boom_line}"
            f"（累计景气 {s.boom_total} 点；档位分冷清/平淡/红火/疯狂，"
            "由当局暴涨暴跌事件的力度与密度决定，每局不同）。"
        )
        lines = []
        for i, e in enumerate(s.entries, 1):
            nm = contexts.clean_name(e.name)
            reason = contexts.REASON_LABELS.get(e.reason, e.reason)
            if e.score is None:
                lines.append(f"{i}. {nm}：身故（健康耗尽客死京城，本局最低档结局）")
                continue
            grade = (
                "欠债离场"
                if e.market_grade == const.GRADE_DEBT
                else const.GRADE_WORDS[e.market_grade]
            )
            lines.append(
                f"{i}. {nm}：{reason}，身家 {e.score} 元（本局盈利 {e.profit} 元），"
                f"在场 {e.days_active} 天赶上景气 {e.boom_seen} 点，成交 {e.trades} 笔，"
                f"系统评级「{grade}」，参考评语：{e.market_verdict}"
            )
        mech = (
            "规则背景：每人开局现金 2000 元、背高利贷 5500 元（日息 10%），存款日息 1%，"
            "身家 = 现金 + 存款 + 存货 − 债务，排名只看身家。"
        )
        fair = (
            "评价标准：必须对照本局行情说话——冷清局赚到小钱也是本事，值得夸；"
            "红火、疯狂局只赚零头就该点破（留情面）；欠债离场与身故是垫底档，"
            "身故最低，但对倒霉人别刻薄。系统评级已按此标准算好，态度以它为准。"
        )
        if solo:
            system_prompt = (
                "你是《北京浮生记》里的老北京说书人，单人局收场，台下坐着唯一的主角。"
                f"{mech}{fair}"
                "用京味儿白话给主角一段收场白：先一句定调（对照行情夸或损），"
                "再点一处最出彩或最遗憾的地方。不超过 60 字，只输出收场白本身，"
                "别复述数字，别加引号。"
            )
        else:
            system_prompt = (
                "你是《北京浮生记》里的老北京说书人，多人局收场，要当众盘点各位的成色。"
                f"{mech}{fair}"
                "用京味儿白话点评：头名要点出，垫底的调侃两句（留情面），中间的可一笔带过；"
                "谁高谁低以系统评级为准，别只看身家大小。不超过 90 字，"
                "只输出点评本身，别复述数字，别加引号。"
            )
        prompt = f"{room_line}\n结算名单：\n" + "\n".join(lines) + "\n请给出收场白。"
        return prompt, system_prompt

    async def _ai_epilogue(self, event: AstrMessageEvent, s: engine.Settlement) -> str:
        """结算后的 LLM 收场白（单人/多人两套提示词，含机制与行情标准）。

        指定供应商累计失败 AI_TRIES 次后回退到会话默认模型；全都失败时
        明确提示「AI 总结失败」，不静默吞掉。
        """
        try:
            plan = self._resolve_ai_providers(event.unified_msg_origin)
        except Exception as e:  # 防御：self.context 形态异常
            logger.warning(f"[浮生记] 说书人供应商解析失败：{e}")
            plan = []
        if not plan:
            return (
                "⚠️ AI 总结失败：没有可用的大模型供应商。\n"
                "💡 在插件配置里用 ai_provider_id 指定一个，或在 WebUI 配好默认对话模型。"
            )
        prompt, system_prompt = self._epilogue_prompts(s)
        last_err = ""
        for provider, tries in plan:
            try:
                label = provider.meta().id
            except Exception:
                label = provider.__class__.__name__
            for attempt in range(1, tries + 1):
                try:
                    resp = await asyncio.wait_for(
                        provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                        timeout=AI_TIMEOUT,
                    )
                    text = (getattr(resp, "completion_text", "") or "").strip()
                    if text:
                        return f"📜 说书人收场白：{text[:160]}"
                    raise RuntimeError("模型返回了空内容")
                except Exception as e:
                    last_err = str(e) or type(e).__name__
                    logger.warning(
                        f"[浮生记] AI 点评失败（{attempt}/{tries}，供应商 {label}）：{e}"
                    )
                    if attempt < tries and self.ai_retry_delay > 0:
                        await asyncio.sleep(self.ai_retry_delay)
        return f"⚠️ AI 总结失败：{last_err[:80]}\n💡 说书人今儿嗓子哑了，各位的战绩以上面的结算特刊为准。"

    # ------------------------------------------------------------------
    # 统一调度
    # ------------------------------------------------------------------

    def _try_auto_skip(self, room: Room, now: float) -> engine.ActionResult | None:
        """当天开始超过 idle_hours 仍有人未行动 -> 按「留守」自动跳过。"""
        hours = float(self._cfg("idle_hours", 24))
        if hours <= 0 or room.phase != PHASE_RUNNING:
            return None
        if not room.waiting_players():
            return None
        if now - room.day_started_at < hours * 3600:
            return None
        try:
            return engine.skip_idlers(room, self.rng, "", True, now, auto=True)
        except GameError:  # 防御：竞态下当作无事发生
            return None

    async def _dispatch(
        self,
        event: AstrMessageEvent,
        action: Callable[[Room, float], engine.ActionResult] | None,
        view: Callable[[Room], _Out] | None = None,
    ) -> AsyncIterator:
        """需要已有房间的命令都走这里：锁 -> 闲置跳天 -> 执行/取景 -> 落盘 -> 出消息。"""
        rid = event.unified_msg_origin
        outs: list[_Out] = []
        async with self._lock(rid):
            room = self._load(rid)
            if room is None:
                outs.append(("text", _NO_GAME_TEXT))
            else:
                now = time.time()
                auto = self._try_auto_skip(room, now)
                if auto is not None:
                    outs += self._apply(rid, room, auto)
                if auto is not None and auto.settlement is not None:
                    outs.append(
                        ("text", "这局拖到打烊，刚按规矩收摊结算了。\n💡 发送「浮生记 创建」再开一局。")
                    )
                else:
                    try:
                        if action is not None:
                            outs += self._apply(rid, room, action(room, now))
                        elif view is not None:
                            outs.append(view(room))
                    except GameError as e:
                        outs.append(("text", e.reply_text()))
        async for r in self._emit(event, outs):
            yield r

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    @filter.command_group("浮生记", alias={"fs", "浮生"})
    def fusheng(self):
        pass

    # ---- 输错命令兜底 ----

    def _soften_bare_wake_word(self) -> None:
        """把指令组过滤器包一层：光发「浮生记」不再由框架报「参数不足」。

        框架对「消息恰好等于指令组名」的处理是抛错并甩出原始指令树，观感
        生硬。软化成按未命中放行后，这条消息会落到 guard_typo，由它回复
        带本群进度的开场引导。框架内部结构对不上时放弃软化（只记日志），
        最多退回框架默认提示，不影响任何命令。
        """
        try:
            from astrbot.core.star.filter.command_group import CommandGroupFilter
            from astrbot.core.star.star_handler import star_handlers_registry

            group_filters = [
                f
                for h in star_handlers_registry.get_handlers_by_module_name(__name__)
                for f in getattr(h, "event_filters", [])
                if isinstance(f, CommandGroupFilter)
                and f.parent_group is None
                and f.group_name == WAKE_WORDS[0]
            ]
            for gf in group_filters:
                if getattr(gf, "_fusheng_bare_softened", False):
                    continue  # 插件重载会重进这里，别套两层
                orig = gf.filter

                def _quiet(event, cfg, _gf=gf, _orig=orig):
                    if _gf.equals(event.message_str.strip()):
                        return False  # 裸唤醒词：这里不响，guard_typo 来引导
                    return _orig(event, cfg)

                gf.filter = _quiet
                gf._fusheng_bare_softened = True
            if not group_filters:
                logger.warning("[浮生记] 未找到指令组过滤器，裸唤醒词仍走框架默认提示")
        except Exception as e:
            logger.warning(f"[浮生记] 裸唤醒词引导未接管（框架结构变动？）：{e}")

    def _welcome_text(self, event: AstrMessageEvent) -> str:
        """裸唤醒词的开场引导：按本群进度给眼下最该用的几条命令。"""
        try:
            room = self._load(event.unified_msg_origin)
        except Exception:  # 引导不能反过来砸场子
            room = None
        if room is not None and room.phase == PHASE_SIGNUP:
            cap = room.setting("max_players", const.MAX_PLAYERS)
            return (
                f"🚉 本群一局候车中（{len(room.players)}/{cap} 人）。\n"
                "💡 「浮生记 加入」上车｜房主「浮生记 开始」发车｜「浮生记 帮助」玩法说明书"
            )
        if room is not None and room.phase == PHASE_RUNNING:
            return (
                f"🀄 本局进行到第 {room.day}/{room.days_total} 天。\n"
                "💡 「浮生记 面板」看账本行情｜「浮生记 排行」看战况｜「浮生记 帮助」全部命令"
            )
        try:
            default_days = int(self._cfg("default_days", const.DEFAULT_DAYS))
        except Exception:
            default_days = const.DEFAULT_DAYS
        return (
            "🀄 《北京浮生记》——揣 2000 块现金、背 5500 高利贷进京，限期倒买倒卖攒身家。\n"
            f"💡 「浮生记 创建 [天数]」开新局（不填默认 {default_days} 天）｜"
            "「浮生记 帮助」玩法说明书｜「浮生记 榜单」历史龙虎榜"
        )

    @staticmethod
    def _closest_subcommand(sub: str) -> str:
        """给输错的子命令找最接近的本名；找不到返回空串。"""
        canon = _SUB_TO_CANON.get(sub.lower())
        if canon:
            return canon
        forms = [f for f in _SUB_TO_CANON if f.startswith(sub) or sub.startswith(f)]
        if not forms:
            forms = difflib.get_close_matches(sub, list(_SUB_TO_CANON), n=1, cutoff=0.5)
        return _SUB_TO_CANON[min(forms, key=len)] if forms else ""

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def guard_typo(self, event: AstrMessageEvent):
        """唤醒词开头但不成命令的消息 -> 当场给提示并终止事件，不透传给 LLM。

        三种情形：光发唤醒词（回一版按本群进度定制的开场引导，替代框架
        生硬的「参数不足」指令树，过滤器已在 __init__ 里软化）；子命令
        打错（报错并给最接近的纠正建议）；唤醒词大小写写岔（提示小写）。
        「浮生记真好玩」这类闲聊不是命令格式，照旧归大模型接茬。
        """
        if not getattr(event, "is_at_or_wake_command", False):
            return  # 没唤醒机器人的消息不归我们管
        tokens = str(getattr(event, "message_str", "") or "").split()
        if not tokens:
            return
        head = tokens[0]
        sub = tokens[1] if len(tokens) > 1 else ""
        if head in WAKE_WORDS:
            if not sub:
                yield event.plain_result(self._welcome_text(event))
                event.stop_event()
                return
            if sub in _SUB_TO_CANON:
                return  # 正经命令，由对应 handler 处理
            guess = self._closest_subcommand(sub)
            hint = f"是不是想说「浮生记 {guess}」？" if guess else ""
            yield event.plain_result(
                f"❓ 浮生记没有「{sub[:12]}」这个命令。\n"
                f"💡 {hint}发送「浮生记 帮助」看全部命令。"
            )
            event.stop_event()
        elif head.lower() == "fs":  # 大小写写岔的 fs：命令同样不会执行，一并拦下提示
            guess = (_SUB_TO_CANON.get(sub) or self._closest_subcommand(sub)) if sub else ""
            hint = f"「浮生记 {guess}」" if guess else "「浮生记 帮助」"
            yield event.plain_result(
                f"❓ 唤醒词「{head[:12]}」要写成小写「fs」（或「浮生记」）。\n"
                f"💡 试试 {hint}。"
            )
            event.stop_event()

    @fusheng.command("创建", alias={"开局", "new"})
    async def cmd_create(self, event: AstrMessageEvent, days: str = ""):
        """创建新对局：浮生记 创建 [天数]"""
        rid = event.unified_msg_origin
        uid, name = self._sender(event)
        outs: list[_Out] = []
        async with self._lock(rid):
            room = self._load(rid)
            if room is not None:
                if room.phase == PHASE_SIGNUP:
                    outs.append(
                        ("text", "已有一局在候车。\n💡 发送「浮生记 加入」上车；房主发送「浮生记 开始」发车。")
                    )
                else:
                    outs.append(
                        ("text", "本群已有一局进行中。\n💡 发送「浮生记 排行」看战况；房主可「浮生记 解散 确认」。")
                    )
            else:
                try:
                    n_days = int(self._cfg("default_days", const.DEFAULT_DAYS))
                    if days.strip():
                        if not days.strip().isdigit():
                            raise GameError(
                                f"天数「{days}」看不懂。",
                                f"用 {const.MIN_DAYS}~{const.MAX_DAYS} 的整数，如「浮生记 创建 40」。",
                            )
                        n_days = int(days.strip())
                    settings = {
                        "max_players": int(self._cfg("max_players", const.MAX_PLAYERS)),
                        "enable_hacker": bool(self._cfg("enable_hacker", False)),
                        "market_impact": bool(self._cfg("market_impact", True)),
                        "intel_price": int(self._cfg("intel_price", const.INTEL_DEFAULT_PRICE)),
                        "intel_accuracy": int(
                            self._cfg("intel_accuracy", const.INTEL_DEFAULT_ACCURACY)
                        ),
                    }
                    room = engine.create_room(rid, uid, name, n_days, settings, time.time())
                except GameError as e:
                    outs.append(("text", e.reply_text()))
                else:
                    self._save(rid, room)
                    outs.append(
                        (
                            "text",
                            f"🍜 新一局《北京浮生记》开张！（{room.days_total} 天 · "
                            f"1/{room.setting('max_players', const.MAX_PLAYERS)} 人）\n"
                            f"{name} 揣着 2,000 元现金、背着 5,500 元高利贷，蹲上了进京的绿皮车。\n"
                            "💡 其他人发送「浮生记 加入」上车（最多 4 人，也可单人开跑）。\n"
                            "💡 房主发送「浮生记 开始」发车；「浮生记 帮助」看玩法说明书。",
                        )
                    )
        async for r in self._emit(event, outs):
            yield r

    @fusheng.command("加入", alias={"上车", "join"})
    async def cmd_join(self, event: AstrMessageEvent):
        """加入候车中的对局"""
        uid, name = self._sender(event)
        async for r in self._dispatch(
            event, lambda room, now: engine.join_room(room, uid, name)
        ):
            yield r

    @fusheng.command("开始", alias={"发车", "start"})
    async def cmd_start(self, event: AstrMessageEvent):
        """房主发车，进入第 1 天"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            result = engine.start_game(room, self.rng, uid, now)
            # 第 1 天没有日报事件，补一份"创刊号"日报图，让大家看到行情
            result.day_reports.append(
                engine.DayReport(
                    day=room.day,
                    days_total=room.days_total,
                    headlines=list(room.headlines),
                    standings=engine.standings(room),
                )
            )
            return result

        async for r in self._dispatch(event, act):
            yield r

    # ---- 每天的行动 ----

    @fusheng.command("去", alias={"前往", "赶路", "go"})
    async def cmd_go(self, event: AstrMessageEvent, dest: str = ""):
        """去某站：浮生记 去 <地点|序号>"""
        uid, _ = self._sender(event)
        async for r in self._dispatch(
            event,
            lambda room, now: engine.move(room, self.rng, uid, parse.parse_location(dest), now),
        ):
            yield r

    @fusheng.command("留守", alias={"原地", "休整", "stay"})
    async def cmd_stay(self, event: AstrMessageEvent):
        """原地休整一天"""
        uid, _ = self._sender(event)
        async for r in self._dispatch(
            event, lambda room, now: engine.move(room, self.rng, uid, None, now)
        ):
            yield r

    @fusheng.command("买", alias={"购买", "进货", "buy"})
    async def cmd_buy(self, event: AstrMessageEvent, good: str = "", qty: str = ""):
        """进货：浮生记 买 <货|序号> <数|全>"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            if not qty.strip():
                raise GameError(
                    "买多少件？",
                    "如「浮生记 买 手机 10」，货可用行情表序号（「浮生记 买 7 全」按现金上限梭哈）。",
                )
            return engine.buy(room, uid, parse.parse_good(good), parse.parse_qty(qty))

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("卖", alias={"出售", "出货", "sell"})
    async def cmd_sell(self, event: AstrMessageEvent, good: str = "", qty: str = ""):
        """出货：浮生记 卖 <货|序号> <数|全>"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            if not qty.strip():
                raise GameError(
                    "卖多少件？",
                    "如「浮生记 卖 手机 10」，货可用行情表序号（「浮生记 卖 7 全」清仓）。",
                )
            return engine.sell(room, uid, parse.parse_good(good), parse.parse_qty(qty))

        async for r in self._dispatch(event, act):
            yield r

    # ---- 周转 ----

    @fusheng.command("存", alias={"存款", "存钱"})
    async def cmd_deposit(self, event: AstrMessageEvent, amount: str = ""):
        """存银行：浮生记 存 <钱|全|半>（不填=全存）"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            p = room.players.get(uid)
            amt = parse.parse_money(amount, p.cash if p else 0) if amount.strip() else None
            return engine.deposit(room, uid, amt)

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("取", alias={"取款", "取钱"})
    async def cmd_withdraw(self, event: AstrMessageEvent, amount: str = ""):
        """取存款：浮生记 取 <钱|全|半>（不填=全取）"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            p = room.players.get(uid)
            amt = parse.parse_money(amount, p.bank if p else 0) if amount.strip() else None
            return engine.withdraw(room, uid, amt)

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("还", alias={"还债", "还钱"})
    async def cmd_repay(self, event: AstrMessageEvent, amount: str = ""):
        """邮局还债：浮生记 还 <钱|全|半>（不填=尽量还清）"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            p = room.players.get(uid)
            amt = parse.parse_money(amount, p.cash if p else 0) if amount.strip() else None
            return engine.repay(room, uid, amt)

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("看病", alias={"治疗", "医院"})
    async def cmd_heal(self, event: AstrMessageEvent, points: str = ""):
        """医院看病：浮生记 看病 <点数|全>（不填=能治多少治多少）"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            pts = parse.parse_qty(points) if points.strip() else None
            return engine.heal(room, uid, pts)

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("租房", alias={"扩容", "搬家"})
    async def cmd_house(self, event: AstrMessageEvent):
        """找中介换大房子（仓库 +10）"""
        uid, _ = self._sender(event)
        async for r in self._dispatch(
            event, lambda room, now: engine.upgrade_house(room, uid)
        ):
            yield r

    @fusheng.command("网吧", alias={"打工", "上网"})
    async def cmd_cafe(self, event: AstrMessageEvent):
        """去网吧打零工挣现钱（每局限 3 次）"""
        uid, _ = self._sender(event)
        async for r in self._dispatch(
            event, lambda room, now: engine.cyber_cafe(room, self.rng, uid)
        ):
            yield r

    @fusheng.command("情报", alias={"消息", "小道消息"})
    async def cmd_intel(self, event: AstrMessageEvent):
        """买明日行情的小道消息（每天一条，真假自辨）"""
        uid, _ = self._sender(event)
        async for r in self._dispatch(
            event, lambda room, now: engine.buy_intel(room, self.rng, uid)
        ):
            yield r

    # ---- 看盘 ----

    @fusheng.command("面板", alias={"状态", "账本", "我"})
    async def cmd_panel(self, event: AstrMessageEvent):
        """自己的随身账本（持仓、盈亏、行情）"""
        uid, _ = self._sender(event)

        def view(room: Room) -> _Out:
            if room.phase != PHASE_RUNNING:
                raise GameError("本局还没开始。", "房主发送「浮生记 开始」发车。")
            if uid not in room.players:
                raise GameError("你不在本局中。", "等这局结束后发送「浮生记 创建」再来。")
            ctx = contexts.panel_context(room, uid)
            return ("image", templates.TMPL_PANEL, ctx, text_fallback.panel_text)

        async for r in self._dispatch(event, None, view):
            yield r

    @fusheng.command("排行", alias={"座次", "战况"})
    async def cmd_rank(self, event: AstrMessageEvent):
        """本局当前座次与行情"""

        def view(room: Room) -> _Out:
            if room.phase != PHASE_RUNNING:
                raise GameError("本局还没开始。", "房主发送「浮生记 开始」发车。")
            return ("image", templates.TMPL_DAY, contexts.rank_context(room), text_fallback.day_text)

        async for r in self._dispatch(event, None, view):
            yield r

    @fusheng.command("榜单", alias={"龙虎榜", "排行榜"})
    async def cmd_board(self, event: AstrMessageEvent):
        """历史龙虎榜（跨局前十）"""
        board = self.store.load_leaderboard()
        if not board:
            board, _ = engine.merge_leaderboard(
                [], engine.Settlement(days_total=0), "-", 0
            )
        ctx = contexts.board_context(board)
        async for r in self._picture(event, templates.TMPL_BOARD, ctx, text_fallback.board_text):
            yield r

    @fusheng.command("帮助", alias={"说明", "玩法", "help"})
    async def cmd_help(self, event: AstrMessageEvent):
        """玩法说明书"""
        ctx = contexts.help_context()
        async for r in self._picture(event, templates.TMPL_HELP, ctx, text_fallback.help_text):
            yield r

    # ---- 房务 ----

    @fusheng.command("跳过", alias={"催", "催场"})
    async def cmd_skip(self, event: AstrMessageEvent):
        """房主催场：把没行动的人按「留守」处理"""
        uid, _ = self._sender(event)
        is_admin = event.is_admin()
        async for r in self._dispatch(
            event,
            lambda room, now: engine.skip_idlers(room, self.rng, uid, is_admin, now),
        ):
            yield r

    @fusheng.command("认输", alias={"投降", "跑路"})
    async def cmd_surrender(self, event: AstrMessageEvent, confirm: str = ""):
        """提前清算离场：浮生记 认输 确认"""
        uid, _ = self._sender(event)

        def act(room: Room, now: float) -> engine.ActionResult:
            if confirm.strip() != "确认":
                raise GameError(
                    "认输会立刻按今日时价清算你的全部家当，提前离场，不能反悔。",
                    "想清楚了就发送「浮生记 认输 确认」。",
                )
            return engine.surrender(room, self.rng, uid, now)

        async for r in self._dispatch(event, act):
            yield r

    @fusheng.command("解散", alias={"散伙"})
    async def cmd_dissolve(self, event: AstrMessageEvent, confirm: str = ""):
        """房主解散本局（不结算不上榜）：浮生记 解散 确认"""
        rid = event.unified_msg_origin
        uid, _ = self._sender(event)
        is_admin = event.is_admin()
        outs: list[_Out] = []
        async with self._lock(rid):
            room = self._load(rid)
            if room is None:
                outs.append(("text", _NO_GAME_TEXT))
            elif confirm.strip() != "确认":
                outs.append(
                    ("text", "解散会直接丢弃本局进度，不结算、不上榜。\n💡 房主想清楚了就发送「浮生记 解散 确认」。")
                )
            else:
                try:
                    engine.dissolve(room, uid, is_admin)
                except GameError as e:
                    outs.append(("text", e.reply_text()))
                else:
                    self._drop(rid)
                    outs.append(
                        ("text", "🎬 本局散伙，各回各家。\n💡 发送「浮生记 创建」随时再开新局。")
                    )
        async for r in self._emit(event, outs):
            yield r
