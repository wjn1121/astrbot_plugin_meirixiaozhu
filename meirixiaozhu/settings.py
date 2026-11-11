# -*- coding: utf-8 -*-
"""配置读取：把 WebUI 面板的值收敛成一个不可变快照，避免各模块到处 .get()。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import DEFAULT_WEIGHTS, normalize_weights

# 面板键 -> (默认值, 下限, 上限)；None 表示不做范围约束
_INT_RULES = {
    "daily_draw_count": (1, 1, 50),
    "pity_threshold": (10, 1, 200),
    "rank_size": (10, 1, 30),
    "sync_batch_size": (5, 0, 50),
    "sync_interval_hours": (24, 1, 720),
    "max_pool_size": (300, 20, 2000),
    "request_timeout": (15, 5, 120),
    "rarity_weight_n": (DEFAULT_WEIGHTS[0], 0, 100),
    "rarity_weight_r": (DEFAULT_WEIGHTS[1], 0, 100),
    "rarity_weight_sr": (DEFAULT_WEIGHTS[2], 0, 100),
}

_FRAME_CHOICES = ("rarity", "simple", "none")
FRAME_LABEL = {"rarity": "按稀有度", "simple": "简洁", "none": "无边框"}

# 六组监听关键词的默认值，与帮助文案、README 说明保持一致
DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "trigger_keywords": ("今日小猪", "每日小猪"),
    "help_keywords": ("帮助", "菜单", "help"),
    "codex_keywords": ("图鉴",),
    "rank_keywords": ("排行", "排名"),
    "stats_keywords": ("池子", "统计"),
    "sync_keywords": ("更新素材", "同步素材"),
}

# 关键词分隔符：中英文逗号、顿号、分号、竖线、空白都算
_SPLIT = re.compile(r"[,，、;；|/\s]+")

# 美元/百万 token 的默认单价，取自 DeepSeek Flash 非高峰价
_DEFAULT_PRICE_IN = 0.15
_DEFAULT_PRICE_OUT = 0.60
_DEFAULT_USD_CNY = 7.1


def split_keywords(raw: object, defaults: tuple[str, ...]) -> tuple[str, ...]:
    """把面板里的关键词串拆成元组。

    raw 为 None（配置里没有这一项）时用默认值；空串表示用户主动清空，
    此时返回空元组——触发词清空即「任意消息都算」，子指令清空即「关闭该子指令」。
    """
    if raw is None:
        return defaults
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    elif isinstance(raw, str):
        items = _SPLIT.split(raw)
    else:
        return defaults

    out: list[str] = []
    for item in items:
        word = item.strip()[:20]
        if word and word not in out:
            out.append(word)
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    """一次抽卡期间使用的配置快照。"""

    daily_draw_count: int = 1
    at_view_pig: bool = False
    need_at: bool = False

    trigger_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["trigger_keywords"]
    help_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["help_keywords"]
    codex_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["codex_keywords"]
    rank_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["rank_keywords"]
    stats_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["stats_keywords"]
    sync_keywords: tuple[str, ...] = DEFAULT_KEYWORDS["sync_keywords"]

    rarity_enabled: bool = True
    pity_enabled: bool = True
    pity_threshold: int = 10
    rarity_weights: tuple[int, int, int, int] = DEFAULT_WEIGHTS

    codex_enabled: bool = True
    rank_enabled: bool = True
    rank_size: int = 10

    card_frame: str = "rarity"

    auto_sync: bool = True
    sync_batch_size: int = 5
    sync_interval_hours: int = 24
    max_pool_size: int = 300
    use_llm_persona: bool = True
    allow_vision: bool = True
    request_timeout: int = 15

    price_input_usd: float = _DEFAULT_PRICE_IN
    price_output_usd: float = _DEFAULT_PRICE_OUT
    usd_cny: float = _DEFAULT_USD_CNY

    debug_log: bool = False

    # ── 关键词匹配 ──────────────────────────────────────────
    @property
    def all_keywords(self) -> tuple[str, ...]:
        return (
            self.trigger_keywords
            + self.help_keywords
            + self.codex_keywords
            + self.rank_keywords
            + self.stats_keywords
            + self.sync_keywords
        )

    def is_triggered(self, text: str) -> bool:
        """消息是否该由本插件接手。

        触发词清空时退化为「任意非空消息都算」，方便只用艾特唤醒的场景。
        """
        if not text:
            return False
        if not self.trigger_keywords:
            return True
        low = text.lower()
        return any(word.lower() in low for word in self.trigger_keywords)

    @staticmethod
    def hits(text: str, words: tuple[str, ...]) -> bool:
        """消息里是否出现任一关键词（英文不区分大小写）。"""
        if not words:
            return False
        low = text.lower()
        return any(word.lower() in low for word in words)

    @classmethod
    def from_config(cls, raw: object) -> "Settings":
        """从 AstrBotConfig 或普通 dict 构造。任何取值异常都退回默认值。"""
        get = getattr(raw, "get", None)
        if not callable(get):
            return cls()

        def _bool(key: str, default: bool) -> bool:
            try:
                return bool(get(key, default))
            except Exception:
                return default

        def _int(key: str) -> int:
            default, low, high = _INT_RULES[key]
            try:
                val = int(get(key, default))
            except Exception:
                return default
            return max(low, min(high, val))

        def _float(key: str, default: float, low: float, high: float) -> float:
            try:
                val = float(get(key, default))
            except Exception:
                return default
            return max(low, min(high, val))

        def _keys(key: str) -> tuple[str, ...]:
            try:
                return split_keywords(get(key, None), DEFAULT_KEYWORDS[key])
            except Exception:
                return DEFAULT_KEYWORDS[key]

        frame = str(get("card_frame", "rarity") or "rarity").strip().lower()
        if frame not in _FRAME_CHOICES:
            frame = "rarity"

        weights = normalize_weights(
            (_int("rarity_weight_n"), _int("rarity_weight_r"), _int("rarity_weight_sr"))
        )

        return cls(
            daily_draw_count=_int("daily_draw_count"),
            at_view_pig=_bool("at_view_pig", False),
            need_at=_bool("need_at", False),
            trigger_keywords=_keys("trigger_keywords"),
            help_keywords=_keys("help_keywords"),
            codex_keywords=_keys("codex_keywords"),
            rank_keywords=_keys("rank_keywords"),
            stats_keywords=_keys("stats_keywords"),
            sync_keywords=_keys("sync_keywords"),
            rarity_enabled=_bool("rarity_enabled", True),
            pity_enabled=_bool("pity_enabled", True),
            pity_threshold=_int("pity_threshold"),
            rarity_weights=weights,
            codex_enabled=_bool("codex_enabled", True),
            rank_enabled=_bool("rank_enabled", True),
            rank_size=_int("rank_size"),
            card_frame=frame,
            auto_sync=_bool("auto_sync", True),
            sync_batch_size=_int("sync_batch_size"),
            sync_interval_hours=_int("sync_interval_hours"),
            max_pool_size=_int("max_pool_size"),
            use_llm_persona=_bool("use_llm_persona", True),
            allow_vision=_bool("allow_vision", True),
            request_timeout=_int("request_timeout"),
            price_input_usd=_float("price_input_usd", _DEFAULT_PRICE_IN, 0.0, 1000.0),
            price_output_usd=_float("price_output_usd", _DEFAULT_PRICE_OUT, 0.0, 1000.0),
            usd_cny=_float("usd_cny", _DEFAULT_USD_CNY, 1.0, 20.0),
            debug_log=_bool("debug_log", False),
        )
