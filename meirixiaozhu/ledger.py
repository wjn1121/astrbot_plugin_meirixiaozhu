# -*- coding: utf-8 -*-
"""抽卡账本：用 sqlite 记录每次抽取，支撑每日次数、图鉴、群排行与保底。"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS draws (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    user_name  TEXT NOT NULL DEFAULT '',
    group_id   TEXT NOT NULL DEFAULT '',
    day        TEXT NOT NULL,
    pig_id     TEXT NOT NULL,
    rarity     TEXT NOT NULL DEFAULT 'N',
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_draws_user_day ON draws(user_id, day);
CREATE INDEX IF NOT EXISTS idx_draws_group    ON draws(group_id, day);

CREATE TABLE IF NOT EXISTS pity (
    user_id TEXT PRIMARY KEY,
    streak  INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class RankRow:
    user_name: str
    collected: int
    legend: int


@dataclass(frozen=True)
class CodexRow:
    unlocked: set[str]
    unlocked_by_rarity: dict[str, set[str]]


class Ledger:
    """单文件 sqlite 账本。数据量小，直接同步访问即可。"""

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        """sqlite3 连接本身不是上下文管理器（只提交不关闭），这里显式关掉。"""
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── 每日次数 ────────────────────────────────────────────
    def count_today(self, user_id: str, day: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM draws WHERE user_id=? AND day=?",
                (user_id, day),
            ).fetchone()
        return int(row["c"]) if row else 0

    def picks_today(self, user_id: str, day: str) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pig_id FROM draws WHERE user_id=? AND day=?",
                (user_id, day),
            ).fetchall()
        return {str(r["pig_id"]) for r in rows}

    def last_today(self, user_id: str, day: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT pig_id FROM draws WHERE user_id=? AND day=? ORDER BY id DESC LIMIT 1",
                (user_id, day),
            ).fetchone()
        return str(row["pig_id"]) if row else None

    # ── 记录 ────────────────────────────────────────────────
    def record(
        self,
        user_id: str,
        user_name: str,
        group_id: str,
        day: str,
        pig_id: str,
        rarity: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO draws (user_id, user_name, group_id, day, pig_id, rarity, ts)"
                " VALUES (?,?,?,?,?,?,?)",
                (user_id, user_name[:64], group_id, day, pig_id, rarity, int(time.time())),
            )

    # ── 保底 ────────────────────────────────────────────────
    def get_pity(self, user_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT streak FROM pity WHERE user_id=?", (user_id,)
            ).fetchone()
        return int(row["streak"]) if row else 0

    def set_pity(self, user_id: str, streak: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pity (user_id, streak) VALUES (?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET streak=excluded.streak",
                (user_id, max(0, streak)),
            )

    # ── 图鉴 ────────────────────────────────────────────────
    def codex(self, user_id: str) -> CodexRow:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT pig_id, rarity FROM draws WHERE user_id=?",
                (user_id,),
            ).fetchall()
        by_rarity: dict[str, set[str]] = {}
        unlocked: set[str] = set()
        for r in rows:
            pid = str(r["pig_id"])
            unlocked.add(pid)
            by_rarity.setdefault(str(r["rarity"]), set()).add(pid)
        return CodexRow(unlocked=unlocked, unlocked_by_rarity=by_rarity)

    def recent_titles(self, user_id: str, limit: int = 3) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pig_id FROM draws WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [str(r["pig_id"]) for r in rows]

    # ── 群排行 ──────────────────────────────────────────────
    def rank_by_collected(self, group_id: str, limit: int) -> list[RankRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id, user_name, COUNT(DISTINCT pig_id) AS c,"
                " SUM(CASE WHEN rarity='SSR' THEN 1 ELSE 0 END) AS s"
                " FROM draws WHERE group_id=?"
                " GROUP BY user_id ORDER BY c DESC, s DESC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        return [
            RankRow(user_name=str(r["user_name"]) or str(r["user_id"])[-4:], collected=int(r["c"]), legend=int(r["s"] or 0))
            for r in rows
        ]

    def rank_by_legend(self, group_id: str, limit: int) -> list[RankRow]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id, user_name, COUNT(DISTINCT pig_id) AS c,"
                " SUM(CASE WHEN rarity='SSR' THEN 1 ELSE 0 END) AS s"
                " FROM draws WHERE group_id=?"
                " GROUP BY user_id ORDER BY s DESC, c DESC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        return [
            RankRow(user_name=str(r["user_name"]) or str(r["user_id"])[-4:], collected=int(r["c"]), legend=int(r["s"] or 0))
            for r in rows
        ]

    def rank_of_user(self, group_id: str, user_id: str) -> tuple[int, int] | None:
        """返回 (名次, 收集数)，不在榜上返回 None。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id, COUNT(DISTINCT pig_id) AS c FROM draws"
                " WHERE group_id=? GROUP BY user_id ORDER BY c DESC",
                (group_id,),
            ).fetchall()
        for i, r in enumerate(rows, 1):
            if str(r["user_id"]) == user_id:
                return i, int(r["c"])
        return None

    # ── 维护 ────────────────────────────────────────────────
    def wipe(self, scope: str = "all") -> int:
        """清空账本。scope=all 连抽取记录一起删，pity 只清保底计数。"""
        with self._conn() as conn:
            if scope in ("all", "draws"):
                cur = conn.execute("DELETE FROM draws")
                rows = cur.rowcount or 0
                conn.execute("DELETE FROM pity")
                return rows
            cur = conn.execute("DELETE FROM pity")
            return cur.rowcount or 0

    # ── 存储统计（供控制台页面使用）─────────────────────────
    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            draws = conn.execute("SELECT COUNT(*) AS c FROM draws").fetchone()["c"]
            users = conn.execute("SELECT COUNT(DISTINCT user_id) AS c FROM draws").fetchone()["c"]
        size = self._path.stat().st_size if self._path.exists() else 0
        return {"rows": int(draws), "users": int(users), "bytes": size}
