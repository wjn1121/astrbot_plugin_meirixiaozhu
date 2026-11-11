# -*- coding: utf-8 -*-
"""每日小猪 —— 插件入口。

只负责生命周期、指令分发与组件装配，业务逻辑都在 meirixiaozhu 包内。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from meirixiaozhu import reply as replies                                       # noqa: E402
from meirixiaozhu.catalog import Catalog, RARITY_ORDER                          # noqa: E402
from meirixiaozhu.card import CardRenderer                                      # noqa: E402
from meirixiaozhu.fonts import FontBook                                         # noqa: E402
from meirixiaozhu.ledger import Ledger                                          # noqa: E402
from meirixiaozhu.service import (                                              # noqa: E402
    DrawOutcome,
    DrawService,
    Exhausted,
    Refusal,
    help_lines,
    today_str,
)
from meirixiaozhu.settings import Settings                                      # noqa: E402
from meirixiaozhu.webui import WebConsole                                       # noqa: E402

PLUGIN_NAME = "astrbot_plugin_meirixiaozhu"

# 装饰器上的正则固定为「任意非空消息」，真正的关键词判定在 on_trigger 里按配置做。
# 这样面板改关键词能立即生效，不必重启插件，也不依赖 AstrBot 内部过滤器可被改写。
TRIGGER_FILTER = r"[\s\S]+"


@register(PLUGIN_NAME, "wjn1121", "抽取属于自己的每日小猪，卡面按稀有度变化", "2.0.0")
class MeiRiXiaoZhuPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.plugin_dir = _PLUGIN_DIR
        self.res_dir = self.plugin_dir / "resource"
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.dynamic_dir = self.data_dir / "dynamic"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dynamic_dir.mkdir(parents=True, exist_ok=True)

        self.fonts = FontBook(self.res_dir / "fonts")
        self.renderer = CardRenderer(self.fonts)
        self.catalog = Catalog(
            builtin_json=self.res_dir / "pigs.json",
            builtin_base=self.res_dir,
            dynamic_dir=self.dynamic_dir,
            weights=self._cfg().rarity_weights,
            names_path=self.data_dir / "names.json",
            texts_path=self.data_dir / "texts.json",
        )
        self.ledger = Ledger(self.data_dir / "ledger.sqlite3")
        self.service = DrawService(self.catalog, self.ledger)

        self._sync_task: asyncio.Task | None = None

        # WebUI 视图接口（存储占用 / 外观预览）；旧版 AstrBot 没有该能力时跳过
        self.web = WebConsole(self)
        try:
            self.web.register(context)
        except Exception as exc:
            logger.warning(f"[每日小猪] WebUI 视图接口未注册：{exc}")

        logger.info(f"[每日小猪] 已载入 {self.catalog.total} 只小猪，数据目录 {self.data_dir}")

    # ── 生命周期 ────────────────────────────────────────────
    async def initialize(self) -> None:
        if not self._cfg().auto_sync:
            return
        try:
            from meirixiaozhu.sync import SyncWorker

            worker = SyncWorker(self.context, self._cfg(), self.catalog, self.data_dir)
            self._sync_task = asyncio.create_task(worker.run_forever())
            logger.info("[每日小猪] 后台素材同步已启动")
        except Exception as exc:
            logger.warning(f"[每日小猪] 素材同步未启动：{exc}")

    async def terminate(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        logger.info("[每日小猪] 已卸载")

    # ── 配置 ────────────────────────────────────────────────
    def _cfg(self) -> Settings:
        return Settings.from_config(self.config)

    def apply_config(self) -> None:
        """面板保存配置后调用：同步稀有度权重并重载猪池。"""
        if self.catalog.set_weights(self._cfg().rarity_weights):
            logger.info(f"[每日小猪] 稀有度权重已更新为 {self.catalog.weights}")

    def _ensure_weights(self) -> None:
        """每次抽卡前对一次权重，兼容用户在 AstrBot 原生配置页改值的情况。"""
        weights = self._cfg().rarity_weights
        if weights != self.catalog.weights:
            self.catalog.set_weights(weights)
            logger.info(f"[每日小猪] 稀有度权重已变更为 {self.catalog.weights}")

    # ── 指令入口 ────────────────────────────────────────────
    @filter.regex(TRIGGER_FILTER)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_trigger(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        cfg = self._cfg()

        if not cfg.is_triggered(text):
            return
        if cfg.need_at and not self._is_woken(event):
            return

        self._ensure_weights()

        if cfg.help_keywords and cfg.hits(text, cfg.help_keywords):
            return await self._cmd_help(event)
        if cfg.codex_keywords and cfg.hits(text, cfg.codex_keywords):
            return await self._cmd_codex(event)
        if cfg.rank_keywords and cfg.hits(text, cfg.rank_keywords):
            return await self._cmd_rank(event)
        if cfg.sync_keywords and cfg.hits(text, cfg.sync_keywords):
            return await self._cmd_sync(event)
        if cfg.stats_keywords and cfg.hits(text, cfg.stats_keywords):
            return await self._cmd_stats(event)
        return await self._cmd_draw(event)

    def _is_woken(self, event: AstrMessageEvent) -> bool:
        """是否艾特了机器人本人。@全体成员 不算。"""
        me = str(event.get_self_id())
        for seg in event.get_messages():
            if isinstance(seg, At) and str(seg.qq) == me:
                return True
        return False

    # ── 帮助 ────────────────────────────────────────────────
    async def _cmd_help(self, event: AstrMessageEvent):
        await event.send(event.plain_result("\n".join(help_lines(self._cfg()))))

    # ── 抽卡 ────────────────────────────────────────────────
    async def _cmd_draw(self, event: AstrMessageEvent):
        cfg = self._cfg()
        sender = str(event.get_sender_id())
        group_id = str(event.get_group_id() or "")
        day = today_str()

        target = self._resolve_target(event, cfg)
        if isinstance(target, Refusal):
            await event.send(event.plain_result(target.text))
            return

        if target and target != sender:
            pig = self.service.peek_other(target, day)
            if pig is None:
                await event.send(event.plain_result("TA 今天还没抽过小猪。"))
                return
            outcome = DrawOutcome(pig=pig, used=0, limit=cfg.daily_draw_count, for_user=target)
            await self._deliver(event, cfg, outcome, mention_id=sender)
            return

        result = self.service.draw(cfg, sender, event.get_sender_name(), group_id, day)

        if isinstance(result, Exhausted):
            # 次数用完：把今天最后那只重发一次 + 一句说明，保证触发就有卡
            if result.pig is None:
                await event.send(event.plain_result(result.text))
                return
            outcome = DrawOutcome(
                pig=result.pig,
                used=result.used,
                limit=result.limit,
                exhausted=True,
            )
            await self._deliver(event, cfg, outcome, mention_id=sender)
            return

        if isinstance(result, Refusal):
            await event.send(event.plain_result(result.text))
            return
        await self._deliver(event, cfg, result, mention_id=sender)

    def _resolve_target(self, event: AstrMessageEvent, cfg: Settings):
        """解析艾特查看的目标：None 表示看自己，Refusal 表示拒绝。"""
        if not cfg.at_view_pig:
            return None

        at_ids = [
            str(seg.qq)
            for seg in event.get_messages()
            if isinstance(seg, At) and str(seg.qq) != str(event.get_self_id())
        ]
        if not at_ids:
            return None
        if len(at_ids) > 1:
            return Refusal("一次只能看一只小猪哦。")

        admins = set(self.context.get_config().get("admins_id", []) or [])
        if at_ids[0] in admins:
            return Refusal("这只猪不能随便看。")
        return at_ids[0]

    async def _deliver(
        self,
        event: AstrMessageEvent,
        cfg: Settings,
        outcome: DrawOutcome,
        mention_id: str,
    ):
        in_group = bool(event.get_group_id())
        png: bytes | None = None
        try:
            png = await asyncio.to_thread(self.renderer.render_png, outcome.pig, cfg.card_frame)
        except Exception as exc:
            logger.error(f"[每日小猪] 卡面渲染失败：{exc}")

        chain = (
            replies.draw_chain(outcome, png, mention_id, in_group)
            if png
            else replies.fallback_chain(outcome, mention_id, in_group)
        )
        try:
            await event.send(event.chain_result(chain))
        except Exception as exc:
            logger.error(f"[每日小猪] 发送失败，转纯文本：{exc}")
            await event.send(event.plain_result(self._plain_outcome(outcome)))

    @staticmethod
    def _plain_outcome(outcome: DrawOutcome) -> str:
        pig = outcome.pig
        return (
            f"【每日小猪】{pig.rarity}\n"
            f"名称：{pig.title}\n一句：{pig.tag}\n解析：{pig.persona}"
        )

    # ── 图鉴 ────────────────────────────────────────────────
    async def _cmd_codex(self, event: AstrMessageEvent):
        if not self._cfg().codex_enabled:
            await event.send(event.plain_result("图鉴功能没开，去后台面板打开吧。"))
            return

        text = (event.message_str or "").upper()
        only = next((r for r in RARITY_ORDER if r in text), None)
        lines = self.service.codex_lines(str(event.get_sender_id()), only)
        await event.send(event.plain_result("\n".join(lines)))

    # ── 排行 ────────────────────────────────────────────────
    async def _cmd_rank(self, event: AstrMessageEvent):
        cfg = self._cfg()
        if not cfg.rank_enabled:
            await event.send(event.plain_result("排行榜没开，去后台面板打开吧。"))
            return

        text = (event.message_str or "").upper()
        by = "legend" if ("欧气" in text or "SSR" in text) else "collected"
        lines = self.service.rank_lines(
            str(event.get_group_id() or ""), str(event.get_sender_id()), by, cfg.rank_size
        )
        await event.send(event.plain_result("\n".join(lines)))

    # ── 池子统计 ────────────────────────────────────────────
    async def _cmd_stats(self, event: AstrMessageEvent):
        counts = self.catalog.count_by_rarity()
        detail = "  ".join(f"{r} {counts.get(r, 0)}" for r in RARITY_ORDER)
        lines = [
            "🐷 当前猪圈",
            "━━━━━━━━━━━━",
            f"总数：{self.catalog.total} 只",
            detail,
        ]
        await event.send(event.plain_result("\n".join(lines)))

    # ── 手动同步 ────────────────────────────────────────────
    async def _cmd_sync(self, event: AstrMessageEvent):
        admins = set(self.context.get_config().get("admins_id", []) or [])
        if str(event.get_sender_id()) not in admins:
            await event.send(event.plain_result("只有管理员能触发素材更新。"))
            return
        try:
            from meirixiaozhu.sync import SyncWorker

            worker = SyncWorker(self.context, self._cfg(), self.catalog, self.data_dir)
            added = await worker.sync_once()
            self.catalog.reload()
            await event.send(event.plain_result(f"素材更新完成，新增 {added} 只。"))
        except Exception as exc:
            logger.error(f"[每日小猪] 手动同步失败：{exc}")
            await event.send(event.plain_result(f"素材更新失败：{exc}"))
