# -*- coding: utf-8 -*-
"""卡面合成：把一只小猪渲染成一张稀有度卡牌，输出 PNG 字节流（不落盘）。

卡面为纯几何程序化绘制，不使用任何外部图片素材，
版式属于通用集换式卡牌布局（标题条 / 插图区 / 文本框 / 页脚）。
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .catalog import Pig
from .fonts import FontBook
from .typeset import CardStyle, fit_lines, measure, new_canvas


@dataclass(frozen=True)
class RarityStyle:
    """一档稀有度的配色与装饰等级。deco 越大装饰越多。"""

    edge_from: tuple[int, int, int]
    edge_to: tuple[int, int, int]
    bar: tuple[int, int, int]
    accent: tuple[int, int, int]
    deco: int
    label: str


RARITY = {
    "N": RarityStyle((150, 155, 164), (198, 202, 208), (120, 126, 136), (150, 155, 164), 0, "NORMAL"),
    "R": RarityStyle((58, 100, 176), (128, 174, 232), (54, 96, 168), (110, 160, 220), 1, "RARE"),
    "SR": RarityStyle((124, 76, 190), (204, 158, 240), (112, 68, 180), (170, 120, 225), 2, "SUPER RARE"),
    "SSR": RarityStyle((196, 138, 40), (250, 226, 150), (186, 128, 34), (230, 180, 70), 3, "LEGEND"),
}

BODY_BG = (251, 249, 244)
THUMB_BG = (246, 243, 235)
TAG_BG = (244, 240, 230)
DESC_BG = (255, 255, 255)
DESC_EDGE = (222, 218, 208)
TITLE_INK = (28, 28, 30)
TAG_INK = (206, 76, 76)
DESC_INK = (74, 72, 68)
FAINT = (140, 136, 128)


def _diagonal_gradient(size: tuple[int, int], c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    """低成本对角渐变：先画小图再放大，避免逐像素遍历。"""
    n = 96
    small = Image.new("RGB", (n, n))
    px = small.load()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2 * (n - 1))
            px[x, y] = (
                int(c1[0] + (c2[0] - c1[0]) * t),
                int(c1[1] + (c2[1] - c1[1]) * t),
                int(c1[2] + (c2[2] - c1[2]) * t),
            )
    return small.resize(size, Image.BICUBIC).convert("RGBA")


_GRADIENT_CACHE: dict[tuple, Image.Image] = {}


def gradient(size: tuple[int, int], c1, c2) -> Image.Image:
    key = (size, c1, c2)
    cached = _GRADIENT_CACHE.get(key)
    if cached is None:
        cached = _diagonal_gradient(size, c1, c2)
        _GRADIENT_CACHE[key] = cached
    return cached


class CardRenderer:
    """把 Pig 渲染成卡面。同一实例可复用，内部有渐变与字体缓存。"""

    def __init__(self, fonts: FontBook, style: CardStyle | None = None):
        self._fonts = fonts
        self._style = style or CardStyle()

    @property
    def style(self) -> CardStyle:
        return self._style

    # ── 对外入口 ────────────────────────────────────────────
    def render_png(self, pig: Pig, frame: str = "rarity", serial: str = "") -> bytes:
        img = self.render_image(pig, frame=frame, serial=serial)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def render_image(self, pig: Pig, frame: str = "rarity", serial: str = "") -> Image.Image:
        st = self._style
        if frame == "none":
            return self._render_plain(pig)

        rs = RARITY.get(pig.rarity, RARITY["N"]) if frame == "rarity" else RARITY["N"]
        canvas, draw = new_canvas(st)

        self._paint_frame(canvas, draw, st, rs)
        self._paint_header(draw, st, rs, pig)
        self._paint_thumb(canvas, draw, st, rs, pig)
        self._paint_tag(draw, st, rs, pig)
        self._paint_body(draw, st, pig)
        self._paint_footer(draw, st, rs, serial or _serial_of(pig))
        return canvas

    # ── 各区块 ──────────────────────────────────────────────
    def _paint_frame(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle) -> None:
        grad = gradient((st.canvas_w, st.canvas_h), rs.edge_from, rs.edge_to)
        mask = Image.new("L", (st.canvas_w, st.canvas_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, st.canvas_w - 1, st.canvas_h - 1], radius=st.corner, fill=255
        )
        canvas.paste(grad, (0, 0), mask)

        inset = st.margin
        draw.rounded_rectangle(
            [inset, inset, st.canvas_w - inset, st.canvas_h - inset],
            radius=st.inner_corner,
            fill=BODY_BG,
            outline=(255, 255, 255),
            width=2,
        )

        if rs.deco >= 2:
            for x in range(inset, st.canvas_w - inset, 26):
                draw.line([(x, inset), (x + 26, inset + 26)], fill=rs.accent + (60,), width=2)
                draw.line([(x, st.canvas_h - inset), (x + 26, st.canvas_h - inset - 26)],
                          fill=rs.accent + (60,), width=2)

        if rs.deco >= 1:
            radius = 13 if rs.deco >= 3 else 9
            for cx, cy in (
                (48, 48),
                (st.canvas_w - 48, 48),
                (48, st.canvas_h - 48),
                (st.canvas_w - 48, st.canvas_h - 48),
            ):
                draw.polygon(
                    [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)],
                    fill=rs.accent,
                )
                if rs.deco >= 3:
                    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255))

    def _paint_header(self, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle, pig: Pig) -> None:
        x1, y1, x2, y2 = st.bar
        draw.rounded_rectangle([x1, y1, x2, y2], radius=st.bar_radius, fill=rs.bar)
        draw.rounded_rectangle(
            [x1, y1, x2, y2], radius=st.bar_radius, outline=(255, 255, 255, 90), width=2
        )

        title = self._fonts.sanitize(pig.title) or "小猪"
        font, tw, th = self._fit_title(draw, title, x2 - x1 - 56)
        draw.text(
            ((st.canvas_w - tw) / 2, y1 + (y2 - y1 - th) / 2 - 6),
            title,
            font=font,
            fill=(255, 255, 255),
        )

        if rs.deco >= 3:
            cx, cy = st.canvas_w // 2, y1 - 26
            draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=rs.accent, outline=(255, 255, 255), width=3)
            draw.polygon(
                [
                    (cx, cy - 11), (cx + 4, cy - 3), (cx + 13, cy - 3), (cx + 6, cy + 3),
                    (cx + 9, cy + 12), (cx, cy + 6), (cx - 9, cy + 12), (cx - 6, cy + 3),
                    (cx - 13, cy - 3), (cx - 4, cy - 3),
                ],
                fill=(255, 255, 255),
            )

    def _fit_title(self, draw: ImageDraw.ImageDraw, title: str, max_w: int):
        """标题过长时逐级缩小字号，保证完整显示不被裁切。"""
        st = self._style
        size = st.title_px
        font = self._fonts.get("title", size, title)
        tw, th = measure(draw, title, font)
        while tw > max_w and size > 30:
            size -= 4
            font = self._fonts.get("title", size, title)
            tw, th = measure(draw, title, font)
        return font, tw, th

    def _paint_thumb(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle, pig: Pig) -> None:
        box = st.thumb
        draw.rounded_rectangle(box, radius=st.box_radius, fill=THUMB_BG, outline=rs.accent, width=3)

        if pig.img is not None:
            try:
                with Image.open(pig.img) as raw:
                    art = raw.convert("RGBA")
                avail_w = box[2] - box[0] - 16
                avail_h = box[3] - box[1] - 16
                art.thumbnail((avail_w, avail_h), Image.LANCZOS)
                px = box[0] + (box[2] - box[0] - art.width) // 2
                py = box[1] + (box[3] - box[1] - art.height) // 2
                canvas.paste(art, (px, py), art if art.mode == "RGBA" else None)
            except (OSError, ValueError):
                self._paint_missing(draw, st, rs, box)
        else:
            self._paint_missing(draw, st, rs, box)

        if rs.deco >= 3:
            self._paint_holo(canvas, box)

    def _paint_missing(self, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle, box) -> None:
        font = self._fonts.get("body", 30)
        text = "图片走丢了"
        tw, th = measure(draw, text, font)
        draw.text(
            ((box[0] + box[2] - tw) / 2, (box[1] + box[3] - th) / 2),
            text,
            font=font,
            fill=FAINT,
        )

    def _paint_holo(self, canvas: Image.Image, box) -> None:
        """传说卡的彩虹全息斜纹，只覆盖插图区域。"""
        w, h = canvas.size
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        step = 30
        for idx, x in enumerate(range(-h, w, step)):
            r, g, b = colorsys.hsv_to_rgb((idx * 0.13) % 1.0, 0.5, 1.0)
            ld.line(
                [(x, box[3]), (x + (box[3] - box[1]), box[1])],
                fill=(int(r * 255), int(g * 255), int(b * 255), 30),
                width=13,
            )
        region = layer.crop(box)
        canvas.paste(Image.alpha_composite(canvas.crop(box).convert("RGBA"), region), box[:2])

    def _paint_tag(self, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle, pig: Pig) -> None:
        x1, y1, x2, y2 = st.tag
        draw.rounded_rectangle([x1, y1, x2, y2], radius=st.tag_radius, fill=TAG_BG, outline=rs.accent, width=2)
        tag = self._fonts.sanitize(pig.tag) or "——"
        font = self._fonts.get("body", st.tag_px, tag)
        text = fit_lines(draw, tag, font, x2 - x1 - 40, 1)[0]
        tw, th = measure(draw, text, font)
        draw.text(((st.canvas_w - tw) / 2, y1 + (y2 - y1 - th) / 2 - 4), text, font=font, fill=TAG_INK)

    def _paint_body(self, draw: ImageDraw.ImageDraw, st: CardStyle, pig: Pig) -> None:
        x1, y1, x2, y2 = st.body
        draw.rounded_rectangle([x1, y1, x2, y2], radius=st.body_radius, fill=DESC_BG, outline=DESC_EDGE, width=2)

        persona = self._fonts.sanitize(pig.persona) or "这只猪还没写简介。"
        font = self._fonts.get("body", st.body_px, persona)
        max_w = x2 - x1 - st.body_pad_x * 2
        max_lines = max(1, (y2 - y1 - st.body_pad_y * 2) // st.body_line_gap)
        lines = fit_lines(draw, persona, font, max_w, max_lines)

        # 行数少时垂直居中，避免下方大片留白
        total_h = len(lines) * st.body_line_gap
        ty = y1 + max(st.body_pad_y, (y2 - y1 - total_h) // 2)
        for line in lines:
            draw.text((x1 + st.body_pad_x, ty), line, font=font, fill=DESC_INK)
            ty += st.body_line_gap

    def _paint_footer(self, draw: ImageDraw.ImageDraw, st: CardStyle, rs: RarityStyle, serial: str) -> None:
        draw.line([(56, st.footer_line_y), (st.canvas_w - 56, st.footer_line_y)], fill=(224, 220, 210), width=2)

        bx1, by1, bx2, by2 = st.footer_badge
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=st.badge_radius, fill=rs.bar)
        font = self._fonts.get("title", st.meta_px, rs.label)
        tw, th = measure(draw, rs.label, font)
        draw.text(
            ((bx1 + bx2 - tw) / 2, by1 + (by2 - by1 - th) / 2 - 2), rs.label, font=font, fill=(255, 255, 255)
        )

        small = self._fonts.get("body", st.small_px, serial)
        sw, sh = measure(draw, serial, small)
        draw.text((st.canvas_w - 56 - sw, st.footer_line_y + 22), serial, font=small, fill=FAINT)

    # ── 简洁模式 ────────────────────────────────────────────
    def _render_plain(self, pig: Pig) -> Image.Image:
        """不画边框的极简版：白底、图、标题、文案。"""
        st = self._style
        canvas = Image.new("RGBA", (st.canvas_w, st.canvas_h), (255, 255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        if pig.img is not None:
            try:
                with Image.open(pig.img) as raw:
                    art = raw.convert("RGBA")
                art.thumbnail((st.canvas_w - 160, 640), Image.LANCZOS)
                canvas.paste(art, ((st.canvas_w - art.width) // 2, 150), art)
            except (OSError, ValueError):
                pass

        tf = self._fonts.get("title", st.title_px, pig.title)
        title = self._fonts.sanitize(pig.title)
        tw, th = measure(draw, title, tf)
        draw.text(((st.canvas_w - tw) / 2, 40), title, font=tf, fill=TITLE_INK)

        persona = self._fonts.sanitize(pig.persona)
        bf = self._fonts.get("body", st.body_px, persona)
        lines = fit_lines(draw, persona, bf, st.canvas_w - 160, 5)
        ty = 820
        for line in lines:
            draw.text((80, ty), line, font=bf, fill=DESC_INK)
            ty += st.body_line_gap
        return canvas


def _serial_of(pig: Pig) -> str:
    if pig.pighub_id is not None:
        return f"No. {pig.pighub_id:04d}"
    return f"No. {abs(hash(pig.pid)) % 10000:04d}"
