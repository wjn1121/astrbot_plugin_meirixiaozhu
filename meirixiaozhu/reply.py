# -*- coding: utf-8 -*-
"""消息组装：把抽卡结果拼成 AstrBot 消息链。"""

from __future__ import annotations

from astrbot.api.message_components import At, Image, Plain

from .service import DrawOutcome


def draw_chain(outcome: DrawOutcome, png: bytes | None, mention_id: str, in_group: bool) -> list:
    """抽卡结果消息链：@ + 一句提示 + 卡面。"""
    parts: list = []
    if in_group and mention_id:
        parts.append(At(qq=mention_id))

    parts.append(Plain(_headline(outcome)))
    if png:
        parts.append(Image.fromBytes(png))
    return parts


def fallback_chain(outcome: DrawOutcome, mention_id: str, in_group: bool) -> list:
    """卡面合成失败时的纯文本降级。"""
    pig = outcome.pig
    parts: list = []
    if in_group and mention_id:
        parts.append(At(qq=mention_id))
    parts.append(
        Plain(
            f"{_headline(outcome)}\n"
            f"名称：{pig.title}\n"
            f"稀有度：{pig.rarity}\n"
            f"一句：{pig.tag}\n"
            f"解析：{pig.persona}"
        )
    )
    return parts


def _headline(outcome: DrawOutcome) -> str:
    pig = outcome.pig
    if outcome.exhausted:
        # 次数用完时重发今天的最后一只，前缀直接说明原因，避免「只回一句没有卡」
        return f" 今日抽取次数已用完（{outcome.used}/{outcome.limit}），这是你今天抽到的小猪："
    if outcome.for_user:
        base = f" TA 今天抽到的是（{pig.rarity}）："
    elif pig.rarity == "SSR":
        base = " 🎉 欧皇降世！这是你的每日小猪："
    elif pig.rarity == "SR":
        base = " 手气不错～这是你的每日小猪："
    else:
        base = " 这是你的每日小猪："

    tail = f"（{outcome.used}/{outcome.limit}）"
    if outcome.pity_fired:
        tail += " · 保底触发"
    return base + tail
