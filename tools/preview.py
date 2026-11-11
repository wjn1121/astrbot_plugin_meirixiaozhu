# -*- coding: utf-8 -*-
"""卡面预览：构建期/调试用，不参与插件运行。

用法：
    python tools/preview.py [输出目录]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from meirixiaozhu.card import CardRenderer          # noqa: E402
from meirixiaozhu.catalog import RARITY_ORDER, Catalog  # noqa: E402
from meirixiaozhu.fonts import FontBook             # noqa: E402


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "_preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    res = BASE / "resource"
    catalog = Catalog(res / "pigs.json", res)
    fonts = FontBook(res / "fonts")
    renderer = CardRenderer(fonts)

    print(f"[catalog] {catalog.total} 只  稀有度 {catalog.count_by_rarity()}")
    print(f"[fonts] {fonts.describe()}")

    picks = []
    for rarity in RARITY_ORDER:
        one = next((p for p in catalog.all if p.rarity == rarity), None)
        if one:
            picks.append(one)

    t0 = time.time()
    for pig in picks:
        png = renderer.render_png(pig, frame="rarity")
        path = out_dir / f"{pig.rarity}_{pig.pighub_id}.png"
        path.write_bytes(png)
        print(f"[ok] {pig.rarity:3s} {pig.title[:12]:14s} {len(png)/1024:6.0f}KB  -> {path.name}")

    plain = renderer.render_png(picks[0], frame="none") if picks else b""
    if plain:
        (out_dir / "mode_none.png").write_bytes(plain)
        print(f"[ok] none 模式 {len(plain)/1024:.0f}KB")

    print(f"[time] {len(picks)} 张共 {time.time()-t0:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
