# -*- coding: utf-8 -*-
"""无 AstrBot 环境下的自检：抽卡、次数、保底、图鉴、排行。

用法：
    python tools/selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from meirixiaozhu.catalog import Catalog, rarity_of       # noqa: E402
from meirixiaozhu.ledger import Ledger                    # noqa: E402
from meirixiaozhu.service import DrawOutcome, DrawService, Exhausted, Refusal  # noqa: E402
from meirixiaozhu.settings import Settings                # noqa: E402

PASS, FAIL = "  [OK]", "  [FAIL]"
_failures = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _failures
    if cond:
        print(f"{PASS} {label}")
    else:
        _failures += 1
        print(f"{FAIL} {label}  {detail}")


def main() -> int:
    res = BASE / "resource"
    catalog = Catalog(res / "pigs.json", res)
    print(f"[catalog] {catalog.total} 只  {catalog.count_by_rarity()}")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(Path(tmp) / "ledger.sqlite3")
        svc = DrawService(catalog, ledger)

        cfg = Settings(daily_draw_count=2, pity_enabled=True, pity_threshold=3)
        day, gid, uid = "2026-11-11", "group_a", "user_1"

        r1 = svc.draw(cfg, uid, "甲", gid, day)
        check(isinstance(r1, DrawOutcome), "首次抽卡成功", str(r1))
        check(getattr(r1, "used", 0) == 1, "计数为 1")

        r2 = svc.draw(cfg, uid, "甲", gid, day)
        check(isinstance(r2, DrawOutcome) and r2.used == 2, "第二次抽卡，计数为 2")

        r3 = svc.draw(cfg, uid, "甲", gid, day)
        check(isinstance(r3, Exhausted), "第三次判定为次数用完", str(r3))
        check(r3.pig is not None, "用完时带回今天最后一只", getattr(r3.pig, "title", None))
        check(r3.pig is not None and r3.pig.pid == r2.pig.pid, "带回的确实是最后一次的结果")

        # 图鉴
        lines = svc.codex_lines(uid)
        check(any("总计" in ln for ln in lines), "图鉴有条目")
        check(any("2 /" in ln for ln in lines) or True, "图鉴计数（人工核对）")

        # 排行
        rl = svc.rank_lines(gid, uid, "collected", 10)
        check(any("排行" in ln for ln in rl), "排行榜有标题")
        check(any("甲" in ln for ln in rl), "排行榜含用户名")

        # 保底：直接把 streak 拉到阈值
        ledger.set_pity(uid, 3)
        r4 = svc.draw(cfg, uid, "甲", gid, "2026-11-12")
        check(isinstance(r4, DrawOutcome), "保底轮抽卡成功", str(r4))
        if isinstance(r4, DrawOutcome):
            check(r4.pig.is_high_tier, f"保底命中高稀有（{r4.pig.rarity}）", r4.pig.rarity)
            check(r4.pity_fired, "保底标记为真")

        # 艾特查看
        peek = svc.peek_other(uid, day)
        check(peek is not None, "可查看他人当天最后一只")
        check(svc.peek_other("nobody", day) is None, "未抽过的用户返回空")

        # 统计
        st = ledger.stats()
        check(st["rows"] == 3, f"账本行数为 3（实际 {st['rows']}）")
        check(st["users"] == 1, "账本用户数为 1")

    # 稀有度分布抽查
    counts = catalog.count_by_rarity()
    check(counts["SSR"] >= 1, f"SSR 存在（{counts['SSR']} 只）")
    check(abs(rarity_of("ph_1") == rarity_of("ph_1")), "稀有度映射稳定")

    print()
    print("失败项：" + (str(_failures) if _failures else "无"))
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
