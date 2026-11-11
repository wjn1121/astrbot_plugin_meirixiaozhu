# -*- coding: utf-8 -*-
"""抽卡业务：把目录、账本、保底串起来，返回结构化结果（不涉及消息组件）。"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from .catalog import RARITY_LABEL, RARITY_ORDER, Catalog, Pig
from .ledger import Ledger
from .settings import FRAME_LABEL, Settings

BAR = "▰"
TRACK = "▱"
TRACK_SLOTS = 8


@dataclass(frozen=True)
class DrawOutcome:
    """一次抽取的结果。"""

    pig: Pig
    used: int                    # 本次之后当天已用次数
    limit: int                   # 当天次数上限
    pity_fired: bool = False     # 本次是否由保底触发
    pity_streak: int = 0         # 当前连续未出高稀有的次数
    for_user: str | None = None  # 艾特查看时的目标用户
    exhausted: bool = False      # 次数已用完，重发的是今天最后一只

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass(frozen=True)
class Refusal:
    """没能抽成的原因，直接作为提示文案返回。"""

    text: str


@dataclass(frozen=True)
class Exhausted:
    """今天次数已用完。

    带上今天最后抽到的那只，让调用方把原卡面重发一次并附一句提示——
    避免出现「关键词触发了但什么都没回」的观感。
    """

    text: str
    pig: Pig | None
    used: int
    limit: int


def today_str() -> str:
    return datetime.date.today().isoformat()


class DrawService:
    def __init__(self, catalog: Catalog, ledger: Ledger):
        self.catalog = catalog
        self.ledger = ledger

    # ── 抽卡 ────────────────────────────────────────────────
    def draw(
        self,
        cfg: Settings,
        user_id: str,
        user_name: str,
        group_id: str,
        day: str | None = None,
    ) -> DrawOutcome | Exhausted | Refusal:
        day = day or today_str()

        used = self.ledger.count_today(user_id, day)
        if used >= cfg.daily_draw_count:
            last_pid = self.ledger.last_today(user_id, day)
            return Exhausted(
                text=f"今天的小猪已经抽完了（{used}/{cfg.daily_draw_count} 次），明天 0 点再来。",
                pig=self.catalog.get(last_pid) if last_pid else None,
                used=used,
                limit=cfg.daily_draw_count,
            )

        streak = self.ledger.get_pity(user_id) if cfg.pity_enabled else 0
        pity_now = cfg.pity_enabled and streak >= cfg.pity_threshold

        pig = self.catalog.pick(self.ledger.picks_today(user_id, day), high_tier_only=pity_now)
        if pig is None:
            return Refusal("猪圈空了，检查一下 resource/pigs.json 是否正常。")

        self.ledger.record(user_id, user_name, group_id, day, pig.pid, pig.rarity)

        if cfg.pity_enabled:
            new_streak = 0 if pig.is_high_tier else streak + 1
            self.ledger.set_pity(user_id, new_streak)
        else:
            new_streak = 0

        return DrawOutcome(
            pig=pig,
            used=used + 1,
            limit=cfg.daily_draw_count,
            pity_fired=pity_now and pig.is_high_tier,
            pity_streak=new_streak,
        )

    def peek_other(self, target_id: str, day: str | None = None) -> Pig | None:
        """艾特查看：取对方当天最后一次抽到的小猪，不消耗次数。"""
        day = day or today_str()
        pid = self.ledger.last_today(target_id, day)
        return self.catalog.get(pid) if pid else None

    # ── 图鉴 ────────────────────────────────────────────────
    def codex_lines(self, user_id: str, only: str | None = None) -> list[str]:
        row = self.ledger.codex(user_id)
        counts = self.catalog.count_by_rarity()
        total = self.catalog.total
        order = [only] if only in RARITY_ORDER else list(RARITY_ORDER)

        lines = ["🐷 每日小猪 · 图鉴", "━━━━━━━━━━━━"]
        lines.append(f"总计    {len(row.unlocked)} / {total}")

        for r in order:
            got = len(row.unlocked_by_rarity.get(r, set()))
            have = counts.get(r, 0)
            lines.append(f"{r:<4}{got:>4} / {have:<4} {_bar(got, have)}")

        titles = [self.catalog.get(pid) for pid in row.unlocked]
        names = [t.title for t in titles if t is not None][-3:]
        if names:
            lines.append("━━━━━━━━━━━━")
            lines.append("最近收集：" + " · ".join(names))

        if only:
            missing = [p.title for p in self.catalog.all if p.rarity == only and p.pid not in row.unlocked]
            if missing:
                lines.append("未解锁：" + " ".join("？？？" for _ in missing[:10]))
        return lines

    # ── 群排行 ──────────────────────────────────────────────
    def rank_lines(
        self, group_id: str, user_id: str = "", by: str = "collected", limit: int = 10
    ) -> list[str]:
        if not group_id:
            return ["排行榜只在群里生效，私聊看不到哦。"]

        rows = (
            self.ledger.rank_by_legend(group_id, limit)
            if by == "legend"
            else self.ledger.rank_by_collected(group_id, limit)
        )
        if not rows:
            return ["本群还没有人抽过小猪，谁是第一个？"]

        title = "🏆 本群每日小猪排行（欧气）" if by == "legend" else "🏆 本群每日小猪排行（收集数）"
        lines = [title, "━━━━━━━━━━━━"]
        for i, row in enumerate(rows, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            name = row.user_name if len(row.user_name) <= 10 else row.user_name[:9] + "…"
            if by == "legend":
                lines.append(f"{medal} {name}  SSR {row.legend} · 收集 {row.collected}")
            else:
                lines.append(f"{medal} {name}  {row.collected} 只 · SSR {row.legend}")

        if user_id:
            mine = self.ledger.rank_of_user(group_id, user_id)
            if mine:
                lines.append("━━━━━━━━━━━━")
                lines.append(f"你的排名：第 {mine[0]} 名 · {mine[1]} 只")
        return lines


def help_lines(cfg: Settings) -> list[str]:
    """帮助文案：格式与图鉴、排行保持一致，关键词全部取当前配置。"""
    head = " / ".join(cfg.trigger_keywords) or "（任意消息）"
    lead = cfg.trigger_keywords[0] if cfg.trigger_keywords else "每日小猪"

    lines = ["🐷 每日小猪 · 帮助", "━━━━━━━━━━━━"]
    lines.append(f"抽卡　{head}")
    lines.append(f"　　　随机抽一只小猪，每天 {cfg.daily_draw_count} 次")

    if not cfg.codex_enabled:
        lines.append("图鉴　（已关闭）")
    elif cfg.codex_keywords:
        lines.append(f"图鉴　{lead} {cfg.codex_keywords[0]} [SR]")
        lines.append("　　　查看个人收集进度，加稀有度只看一档")

    if not cfg.rank_enabled:
        lines.append("排行　（已关闭）")
    elif cfg.rank_keywords:
        lines.append(f"排行　{lead} {cfg.rank_keywords[0]} [欧气]")
        lines.append("　　　本群收集数排行，加「欧气」看 SSR 数量")

    if cfg.stats_keywords:
        lines.append(f"池子　{lead} {cfg.stats_keywords[0]}")
        lines.append("　　　查看猪圈规模与稀有度分布")

    if cfg.sync_keywords:
        lines.append(f"更新　{lead} {cfg.sync_keywords[0]}")
        lines.append("　　　立刻从 PigHub 扩充素材（仅管理员）")

    if cfg.at_view_pig:
        lines.append(f"看猪　{lead} @某人")
        lines.append("　　　看对方今天抽到的小猪")

    if cfg.help_keywords:
        lines.append(f"帮助　{lead} {cfg.help_keywords[0]}")
        lines.append("　　　就是这一条")

    status = [f"{cfg.daily_draw_count} 次/天"]
    status.append(f"保底 {cfg.pity_threshold} 次" if cfg.pity_enabled else "保底关闭")
    status.append(FRAME_LABEL.get(cfg.card_frame, "按稀有度") + "卡框")
    if cfg.need_at:
        status.append("需艾特唤醒")
    lines.append("━━━━━━━━━━━━")
    lines.append("当前：" + " · ".join(status))
    return lines


def _bar(got: int, total: int) -> str:
    if total <= 0:
        return TRACK * TRACK_SLOTS
    filled = int(round(got / total * TRACK_SLOTS))
    filled = max(0, min(TRACK_SLOTS, filled))
    return BAR * filled + TRACK * (TRACK_SLOTS - filled)


def rarity_caption(pig: Pig) -> str:
    return RARITY_LABEL.get(pig.rarity, "NORMAL")
