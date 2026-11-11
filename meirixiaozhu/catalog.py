# -*- coding: utf-8 -*-
"""小猪目录：加载内置猪库、合并运行时扩充的动态猪库，并提供抽取。"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

# 稀有度从低到高
RARITY_ORDER = ("N", "R", "SR", "SSR")
RARITY_LABEL = {
    "N": "NORMAL",
    "R": "RARE",
    "SR": "SUPER RARE",
    "SSR": "LEGEND",
}
HIGH_TIER = frozenset({"SR", "SSR"})

SCHEMA_VERSION = 2

# N / R / SR / SSR 四档的默认占比，SSR 只影响 5% 的猪
DEFAULT_WEIGHTS = (45, 33, 17, 5)


def normalize_weights(raw: object) -> tuple[int, int, int, int]:
    """把面板上的三个百分比收敛成四档权重，SSR 自动补足剩余。

    前三维之和超过 99 时按比例压缩，保证 SSR 至少留 1%。
    """
    try:
        n, r, sr = (max(0, int(x)) for x in list(raw)[:3])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS
    total = n + r + sr
    if total > 99:
        n = int(n * 99 / total)
        r = int(r * 99 / total)
        sr = 99 - n - r
    return (n, r, sr, 100 - n - r - sr)


def rarity_of(pid: str, weights: tuple[int, int, int, int] = DEFAULT_WEIGHTS) -> str:
    """按 pid 稳定映射稀有度。

    取 sha1 后 8 位取模；150 只实测分布最接近目标（SSR 命中 8 只），
    优于取前 8 位（SSR 仅 2 只）。
    """
    n, r, sr, _ = weights
    h = int(hashlib.sha1(pid.encode()).hexdigest()[32:], 16) % 100
    if h < n:
        return "N"
    if h < n + r:
        return "R"
    if h < n + r + sr:
        return "SR"
    return "SSR"


def next_rarity(
    counts: dict[str, int], weights: tuple[int, int, int, int] = DEFAULT_WEIGHTS
) -> str:
    """给新增的猪挑一个稀有度，让整体占比始终贴近目标权重。

    按「目标只数 − 现有只数」算缺口，缺口最大的那档优先；缺口相同时取低档，
    避免小样本阶段把新猪全堆成 SSR。这样无论池子多大，四档占比都会自动收敛。
    """
    total = sum(int(counts.get(r, 0)) for r in RARITY_ORDER) + 1
    best = RARITY_ORDER[0]
    best_gap: float | None = None
    for rarity, weight in zip(RARITY_ORDER, weights):
        gap = total * weight / 100.0 - int(counts.get(rarity, 0))
        if best_gap is None or gap > best_gap + 1e-9:
            best, best_gap = rarity, gap
    return best


@dataclass(frozen=True)
class Pig:
    """一只小猪。img 已解析为绝对路径，渲染时直接用。"""

    pid: str
    title: str
    tag: str
    persona: str
    rarity: str
    img: Path | None
    origin: str = "builtin"
    pighub_id: int | None = None

    @property
    def is_high_tier(self) -> bool:
        return self.rarity in HIGH_TIER


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _parse_entries(
    doc: dict,
    base_dir: Path,
    origin: str,
    weights: tuple[int, int, int, int] = DEFAULT_WEIGHTS,
    names: dict[str, str] | None = None,
    texts: dict[str, dict[str, str]] | None = None,
    honor_rarity: bool = False,
) -> tuple[list[Pig], dict[str, tuple[str, str, str]]]:
    """把一份 pigs.json 解析成 Pig 列表，图片路径相对 base_dir 解析。

    稀有度：内置素材按权重实时哈希（改概率后整池重新分级）；动态素材用入库时
    算好的档位（honor_rarity），保证新增时能按总量比例补齐。
    names / texts 是用户在面板里改过的显示名与文案，优先于素材自带的内容。
    第二个返回值是每只猪的原始文案，供面板做「恢复默认」时回填 placeholder。
    """
    out: list[Pig] = []
    defaults: dict[str, tuple[str, str, str]] = {}
    names = names or {}
    texts = texts or {}
    for item in doc.get("pigs") or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("pid") or "").strip()
        raw_title = str(item.get("title") or "").strip()
        if not pid or not raw_title:
            continue
        raw_tag = str(item.get("tag") or "").strip()
        raw_persona = str(item.get("persona") or "").strip()
        override = texts.get(pid) or {}

        img_rel = str(item.get("img") or "").strip()
        img_path = (base_dir / img_rel) if img_rel else None
        if img_path is not None and not img_path.exists():
            img_path = None

        if honor_rarity:
            stored = str(item.get("rarity") or "").upper()
            rarity = stored if stored in RARITY_LABEL else rarity_of(pid, weights)
        else:
            rarity = rarity_of(pid, weights)

        out.append(
            Pig(
                pid=pid,
                title=names.get(pid) or raw_title,
                tag=override.get("tag") or raw_tag,
                persona=override.get("persona") or raw_persona,
                rarity=rarity,
                img=img_path,
                origin=str(item.get("origin") or origin),
                pighub_id=(
                    int(item["pighub_id"])
                    if isinstance(item.get("pighub_id"), int)
                    else None
                ),
            )
        )
        defaults[pid] = (raw_title, raw_tag, raw_persona)
    return out, defaults


class Catalog:
    """内置 + 动态两层猪库。动态层由联网同步写入，不占插件体积。"""

    def __init__(
        self,
        builtin_json: Path,
        builtin_base: Path,
        dynamic_dir: Path | None = None,
        weights: object = None,
        names_path: Path | None = None,
        texts_path: Path | None = None,
    ):
        self._builtin_json = builtin_json
        self._builtin_base = builtin_base
        self._dynamic_dir = dynamic_dir
        self._weights = normalize_weights(weights) if weights is not None else DEFAULT_WEIGHTS
        self._names_path = names_path
        self._texts_path = texts_path
        self._names: dict[str, str] = self._load_names()
        self._texts: dict[str, dict[str, str]] = self._load_texts()
        self._defaults: dict[str, tuple[str, str, str]] = {}
        self._pigs: list[Pig] = []
        self._index: dict[str, Pig] = {}
        self.reload()

    # ── 加载 ────────────────────────────────────────────────
    def reload(self) -> int:
        pigs: list[Pig] = []
        defaults: dict[str, tuple[str, str, str]] = {}

        doc = _read_json(self._builtin_json)
        if doc:
            parsed, plain = _parse_entries(
                doc, self._builtin_base, "builtin", self._weights, self._names, self._texts
            )
            pigs.extend(parsed)
            defaults.update(plain)

        if self._dynamic_dir is not None:
            dyn = _read_json(self._dynamic_dir / "pigs.json")
            if dyn:
                parsed, plain = _parse_entries(
                    dyn,
                    self._dynamic_dir,
                    "dynamic",
                    self._weights,
                    self._names,
                    self._texts,
                    honor_rarity=True,
                )
                pigs.extend(parsed)
                defaults.update(plain)

        # 去重：同 pid 以内置优先（先出现者保留）
        seen: set[str] = set()
        unique: list[Pig] = []
        for pig in pigs:
            if pig.pid in seen:
                continue
            seen.add(pig.pid)
            unique.append(pig)

        self._pigs = unique
        self._index = {p.pid: p for p in unique}
        self._defaults = defaults
        return len(unique)

    # ── 权重与命名 ──────────────────────────────────────────
    @property
    def weights(self) -> tuple[int, int, int, int]:
        return self._weights

    def set_weights(self, weights: object) -> bool:
        """更新稀有度权重，变了才重新解析。返回是否发生了变化。"""
        new = normalize_weights(weights)
        if new == self._weights:
            return False
        self._weights = new
        self.reload()
        return True

    @property
    def names(self) -> dict[str, str]:
        return dict(self._names)

    def rename(self, pid: str, name: str) -> bool:
        """改一只猪的显示名；name 留空表示恢复原始标题。"""
        if not pid or pid not in self._index:
            return False
        clean = (name or "").strip()[:40]
        if clean:
            self._names[pid] = clean
        else:
            self._names.pop(pid, None)
        self._save_names()
        self.reload()
        return True

    def _load_names(self) -> dict[str, str]:
        if not self._names_path or not self._names_path.exists():
            return {}
        try:
            data = json.loads(self._names_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}

    def _save_names(self) -> None:
        if not self._names_path:
            return
        try:
            self._names_path.parent.mkdir(parents=True, exist_ok=True)
            self._names_path.write_text(
                json.dumps(self._names, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ── 文案覆盖（一句话 / 描述）────────────────────────────
    @property
    def texts(self) -> dict[str, dict[str, str]]:
        return {pid: dict(item) for pid, item in self._texts.items()}

    def default_text(self, pid: str) -> tuple[str, str, str]:
        """这只猪的原始（标题, 一句话, 描述），未加载到时返回空串。"""
        return self._defaults.get(pid, ("", "", ""))

    def set_text(self, pid: str, field: str, value: str) -> bool:
        """改「一句话」或「描述」；value 留空表示恢复素材原文。"""
        if field not in ("tag", "persona") or pid not in self._index:
            return False
        clean = (value or "").strip()[:400]
        item = dict(self._texts.get(pid) or {})
        if clean:
            item[field] = clean
        else:
            item.pop(field, None)
        if item:
            self._texts[pid] = item
        else:
            self._texts.pop(pid, None)
        self._save_texts()
        self.reload()
        return True

    def set_title(self, pid: str, value: str) -> bool:
        return self.rename(pid, value)

    def clear_texts(self) -> int:
        """清掉全部文案覆盖，返回受影响的数量。"""
        count = len(self._texts)
        self._texts = {}
        self._save_texts()
        self.reload()
        return count

    def _load_texts(self) -> dict[str, dict[str, str]]:
        if not self._texts_path or not self._texts_path.exists():
            return {}
        try:
            data = json.loads(self._texts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for pid, item in data.items():
            if not isinstance(item, dict):
                continue
            cleaned = {
                k: str(v).strip()
                for k, v in item.items()
                if k in ("tag", "persona") and str(v).strip()
            }
            if cleaned:
                out[str(pid)] = cleaned
        return out

    def _save_texts(self) -> None:
        if not self._texts_path:
            return
        try:
            self._texts_path.parent.mkdir(parents=True, exist_ok=True)
            self._texts_path.write_text(
                json.dumps(self._texts, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ── 查询 ────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return len(self._pigs)

    @property
    def all(self) -> list[Pig]:
        return list(self._pigs)

    def get(self, pid: str) -> Pig | None:
        return self._index.get(pid)

    def by_pighub_id(self, pighub_id: int) -> Pig | None:
        for pig in self._pigs:
            if pig.pighub_id == pighub_id:
                return pig
        return None

    def count_by_rarity(self) -> dict[str, int]:
        counts = {r: 0 for r in RARITY_ORDER}
        for pig in self._pigs:
            counts[pig.rarity] += 1
        return counts

    # ── 抽取 ────────────────────────────────────────────────
    def pick(self, avoid: set[str] | None = None, high_tier_only: bool = False) -> Pig | None:
        """随机抽一只。

        avoid: 当天已抽过的 pid 集合，优先避开；避不开则从全量抽。
        high_tier_only: 保底触发时只从 SR/SSR 中抽。
        """
        pool = [p for p in self._pigs if p.is_high_tier] if high_tier_only else self._pigs
        if not pool:
            pool = self._pigs
        if not pool:
            return None

        if avoid:
            fresh = [p for p in pool if p.pid not in avoid]
            if fresh:
                return random.choice(fresh)
        return random.choice(pool)
