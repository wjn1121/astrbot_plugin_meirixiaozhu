# -*- coding: utf-8 -*-
"""排版：卡片尺寸规格、文字度量与断行。"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

# 不能出现在行首的标点（避头）
_NO_LINE_START = set("，。！？；：、）】」』》〉…·%”’")
# 不能出现在行尾的标点（避尾）
_NO_LINE_END = set("（【「『《〈“‘")


@dataclass(frozen=True)
class CardStyle:
    """一张卡片的全部尺寸与字形规格。改这里就能调整版式。"""

    canvas_w: int = 960
    canvas_h: int = 1200

    margin: int = 20          # 外框到画布边缘
    border: int = 18          # 边框厚度
    corner: int = 34          # 外框圆角
    inner_corner: int = 24    # 卡身圆角

    bar: tuple[int, int, int, int] = (56, 56, 926, 168)          # 标题栏
    thumb: tuple[int, int, int, int] = (56, 188, 904, 690)       # 插图区
    tag: tuple[int, int, int, int] = (56, 712, 904, 782)         # 一句钩子
    body: tuple[int, int, int, int] = (56, 800, 904, 1074)       # 性格解析
    footer_line_y: int = 1098
    footer_badge: tuple[int, int, int, int] = (56, 1114, 250, 1156)

    title_px: int = 68
    tag_px: int = 34
    body_px: int = 29
    meta_px: int = 24
    small_px: int = 20

    bar_radius: int = 16
    box_radius: int = 14
    tag_radius: int = 12
    body_radius: int = 12
    badge_radius: int = 8

    body_line_gap: int = 46
    body_pad_x: int = 20
    body_pad_y: int = 26


def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """单行文本的宽高。"""
    if not text:
        return 0, 0
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """按宽度断行，尽量把标点留在行尾、避免行首出现标点。"""
    if not text:
        return []
    lines: list[str] = []
    current: list[str] = []

    def width_of(chars: list[str]) -> float:
        return draw.textlength("".join(chars), font=font)

    for ch in text:
        if ch == "\n":
            lines.append("".join(current))
            current = []
            continue

        candidate = current + [ch]
        if width_of(candidate) <= max_width or not current:
            current.append(ch)
            continue

        # 超宽，需要在 current 里找断点
        cut = len(current)
        # 行尾是开标点，往前挪一个，避免标点被掰到下一行
        while cut > 1 and current[cut - 1] in _NO_LINE_END:
            cut -= 1
        line = current[:cut]
        rest = current[cut:]
        lines.append("".join(line))
        current = rest + [ch]

    if current:
        lines.append("".join(current))

    # 收尾修整：行首标点与前一行合并
    fixed: list[str] = []
    for line in lines:
        if fixed and line and line[0] in _NO_LINE_START:
            fixed[-1] += line[0]
            line = line[1:]
            if not line:
                continue
        fixed.append(line)
    return fixed


def fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
    ellipsis: str = "…",
) -> list[str]:
    """断行并限制行数，超出时末行加省略号。"""
    lines = wrap(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines

    kept = lines[:max_lines]
    last = kept[-1]
    while last and draw.textlength(last + ellipsis, font=font) > max_width:
        last = last[:-1]
    kept[-1] = last + ellipsis
    return kept


def new_canvas(style: CardStyle) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    canvas = Image.new("RGBA", (style.canvas_w, style.canvas_h), (0, 0, 0, 0))
    return canvas, ImageDraw.Draw(canvas)
