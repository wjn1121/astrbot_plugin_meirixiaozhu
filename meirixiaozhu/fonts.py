# -*- coding: utf-8 -*-
"""字体装载与回退。

两级字体：标题用圆胖的庆科黄油体，正文用马克笔手写感的霞鹜漫黑。
两者都是子集字体，构建期会同时产出字符集清单，运行期据此判断是否需要回退，
避免生僻字渲染成方块。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import ImageFont

TITLE_FILE = "qingke-huangyou.ttf"
BODY_FILE = "marker-gothic.ttf"
TITLE_CHARSET = "charset-title.txt"
BODY_CHARSET = "charset-body.txt"

# 系统字体兜底：按平台分目录 + 文件名，逐个探测
_SYSTEM_FONTS = {
    "win32": (Path("C:/Windows/Fonts"), ("msyh.ttc", "simhei.ttf", "simsun.ttc")),
    "linux": (
        Path("/usr/share/fonts"),
        (
            "opentype/noto/NotoSansCJK-Regular.ttc",
            "truetype/dejavu/DejaVuSans.ttf",
        ),
    ),
    "darwin": (Path("/System/Library/Fonts"), ("PingFang.ttc", "Hiragino Sans GB.ttc")),
}


def system_font_candidates() -> list[Path]:
    """当前平台优先，其余平台随后，便于容器里也能兜住。"""
    order = [sys.platform] + [k for k in _SYSTEM_FONTS if k != sys.platform]
    out: list[Path] = []
    for key in order:
        entry = _SYSTEM_FONTS.get(key)
        if entry is None:
            continue
        root, names = entry
        out.extend(root / name for name in names)
    return out


def _load_charset(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    try:
        return frozenset(path.read_text(encoding="utf-8").strip())
    except OSError:
        return frozenset()


class FontBook:
    """按角色取字体，并在缺字时自动降级。"""

    def __init__(self, font_dir: Path):
        self._dir = font_dir
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._title_chars = _load_charset(font_dir / TITLE_CHARSET)
        self._body_chars = _load_charset(font_dir / BODY_CHARSET)
        self._fallback_path = self._find_system_font()

    @staticmethod
    def _find_system_font() -> str | None:
        for path in system_font_candidates():
            if path.exists():
                return str(path)
        return None

    # ── 能力查询 ────────────────────────────────────────────
    def title_supports(self, text: str) -> bool:
        """标题字体能否完整渲染这段文字。没有字符集清单时默认相信它。"""
        if not self._title_chars:
            return True
        return all(ch in self._title_chars for ch in text if not ch.isspace())

    def body_supports(self, text: str) -> bool:
        if not self._body_chars:
            return True
        return all(ch in self._body_chars for ch in text if not ch.isspace())

    def has_glyph(self, ch: str) -> bool:
        """插件内两款字体是否有这个字形。都没有就该剥离（如 emoji）。"""
        if ch.isspace():
            return True
        return ch in self._title_chars or ch in self._body_chars

    def sanitize(self, text: str, placeholder: str = "") -> str:
        """剥掉两款字体都渲染不了的字符，避免卡面出现方块。"""
        if not self._title_chars and not self._body_chars:
            return text
        return "".join(ch if self.has_glyph(ch) else placeholder for ch in text)

    # ── 取字体 ──────────────────────────────────────────────
    def get(self, role: str, size: int, text: str = "") -> ImageFont.ImageFont:
        """role: title | body。带 text 时会检查缺字并自动回退。"""
        if role == "title" and text and not self.title_supports(text):
            role = "body"

        key = (role, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        font = self._build(role, size, text)
        if isinstance(font, ImageFont.FreeTypeFont):
            self._cache[key] = font
        return font

    def _build(self, role: str, size: int, text: str) -> ImageFont.ImageFont:
        name = TITLE_FILE if role == "title" else BODY_FILE
        path = self._dir / name
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass

        # 插件字体不可用：正文缺字时再退一级到系统字体
        if role == "body" and text and not self.body_supports(text) and self._fallback_path:
            try:
                return ImageFont.truetype(self._fallback_path, size)
            except OSError:
                pass
        if self._fallback_path:
            try:
                return ImageFont.truetype(self._fallback_path, size)
            except OSError:
                pass
        return ImageFont.load_default()

    # ── 诊断 ────────────────────────────────────────────────
    def describe(self) -> dict[str, object]:
        def _info(name: str, chars: frozenset[str]) -> dict[str, object]:
            p = self._dir / name
            return {
                "file": name,
                "exists": p.exists(),
                "bytes": p.stat().st_size if p.exists() else 0,
                "charset": len(chars),
            }

        return {
            "title": _info(TITLE_FILE, self._title_chars),
            "body": _info(BODY_FILE, self._body_chars),
            "system_fallback": self._fallback_path,
        }
