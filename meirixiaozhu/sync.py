# -*- coding: utf-8 -*-
"""运行时素材扩充：从 PigHub 拉新图，并为其生成文案。

文案生成三级降级：
  L1 多模态 —— 模型支持读图时，把图片和写作规范一起发过去
  L2 纯文本 —— 模型不支持读图，只给标题
  L3 模板   —— 没有可用模型或两次校验都没过，用本地模板拼装

生成的素材写入插件数据目录，不计入插件包体积。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image

from astrbot.api import logger

from .catalog import Catalog, next_rarity
from .settings import Settings

API_URL = "https://www.pighub.top/api/images?sort=0"
IMAGE_BASE = "https://www.pighub.top"
HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_DIM = 640
WEBP_Q = 82
MAX_BYTES = 180 * 1024

# 与原作 96 条文案做查重用的阈值：连续 5 个字重合即视为雷同
NGRAM = 5

_TAG_POOL = (
    "这只猪有点东西",
    "看着眼熟，就是想不起来",
    "气质独特，说不出哪里独特",
    "一眼难忘，二眼还是难忘",
    "全场最稳的一只",
    "来都来了，抽都抽了",
    "长得很有想法",
    "安静地做一只好猪",
    "它好像有话要说",
    "装备全靠捡",
    "精神状态相当稳定",
    "不太聪明的样子",
    "疑似有点社恐",
    "正在加载中",
    "谁家猪跑出来了",
    "此处应有掌声",
)

_PERSONA_POOL = (
    "「{t}」在猪圈里小有名气，出名原因至今没人说得清。它自己也不太清楚，但每次被问起都点头承认，于是传闻越滚越大。",
    "「{t}」的日常只有两件事：找吃的，以及思考下一顿吃什么。中间偶尔穿插一点人生哲理，但通常想到一半就睡着了。",
    "没人知道「{t}」是哪天出现在猪圈的。它自己说是路过，结果一住就是很久，还把角落那张草垫占成了自己的地盘。",
    "「{t}」保持着猪圈里最难破的纪录：连续被人夸奖而毫不动容。有群友说这叫稳重，也有人说它只是没听懂。",
    "「{t}」最擅长的能力是出现在合照的边缘位置，然后靠一个表情抢走全部注意力。当事人对此表示：不是故意的。",
    "关于「{t}」，猪圈流传着三个版本的故事，一个比一个离谱，而它本人每次都听得很认真，最后还要补充两句细节。",
    "「{t}」每天的日程排得很满：上午发呆，下午发呆，晚上总结白天的发呆成果。有猪问它累不累，它说还行。",
    "「{t}」对世界抱有朴素的信任：看到吃的就上，看到凳子就坐，看到镜头就摆姿势。至今没吃过什么亏，运气好得离谱。",
)


@dataclass(frozen=True)
class Draft:
    tag: str
    persona: str


class SyncWorker:
    def __init__(self, context, cfg: Settings, catalog: Catalog, data_dir: Path):
        self.context = context
        self.cfg = cfg
        self.catalog = catalog
        self.data_dir = data_dir
        self.dynamic_dir = data_dir / "dynamic"
        self.img_dir = self.dynamic_dir / "images"
        self.store = self.dynamic_dir / "pigs.json"
        self._provider_inst = None
        self._provider_warned = False
        self._no_image = False          # 模型确认读不了图后，本轮不再带图
        self.usage_path = data_dir / "usage.json"
        self.state_path = data_dir / "sync_state.json"
        self._usage = self._load_usage()

    # ── 循环 ────────────────────────────────────────────────
    async def run_forever(self) -> None:
        # AstrBot 先加载插件、后初始化模型，这里等模型就绪再开始，
        # 否则首轮会把「模型还没加载」误判成「没有模型」而全部降级成模板。
        await self._await_provider()
        while True:
            if self._should_sync():
                try:
                    added = await self.sync_once()
                    if added:
                        logger.info(f"[每日小猪] 本轮扩充 {added} 只，当前猪池 {self.catalog.total} 只")
                        self.catalog.reload()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(f"[每日小猪] 自动同步失败：{type(exc).__name__}: {exc}")
            else:
                left = self._next_sync_in()
                logger.info(f"[每日小猪] 距上次扩充不足间隔，{left / 3600:.1f} 小时后再试")
            await asyncio.sleep(max(1, self.cfg.sync_interval_hours) * 3600)

    # ── 同步节奏 ────────────────────────────────────────────
    def _last_sync(self) -> float:
        """上次成功扩充的时间戳，没有记录时返回 0。"""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return float(data.get("last_sync", 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0.0

    def _mark_sync(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"last_sync": time.time()}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _next_sync_in(self) -> float:
        floor = min(self.cfg.sync_interval_hours * 3600, 6 * 3600)
        return max(0.0, floor - (time.time() - self._last_sync()))

    def _should_sync(self) -> bool:
        """重启后不要立刻重拉：距上次不足半个间隔（最多 6 小时）就跳过。"""
        last = self._last_sync()
        if last <= 0:
            return True
        return time.time() - last >= min(self.cfg.sync_interval_hours * 3600, 6 * 3600)

    # ── 单轮 ────────────────────────────────────────────────
    async def sync_once(self, batch: int | None = None) -> int:
        """拉一轮新素材。batch 为空时用配置里的每轮数量。"""
        target_cfg = batch if batch is not None else self.cfg.sync_batch_size
        if target_cfg <= 0:
            return 0

        # 多取一些候选，下载失败的可以顺位补上
        incoming = await asyncio.to_thread(self._fetch_new, target_cfg)
        if not incoming:
            logger.info("[每日小猪] PigHub 暂无新图可扩充")
            return 0

        existing = self._load_store()
        known = {int(e.get("pighub_id", 0)) for e in existing.get("pigs", [])}
        pool_size = self.catalog.total + len(existing.get("pigs", []))
        budget = max(0, self.cfg.max_pool_size - pool_size)
        if budget <= 0:
            logger.info(f"[每日小猪] 猪池已达上限 {self.cfg.max_pool_size}，跳过扩充")
            return 0

        target = min(budget, target_cfg)
        added = 0
        # 新增猪按当前四档占比补齐：每加一只都重新算一次缺口，
        # 池子越大越贴近目标权重，不会出现「新拉的全是 N」或「SSR 扎堆」。
        counts = self.catalog.count_by_rarity()
        for meta in incoming:
            if added >= target:
                break
            pid_int = int(meta["id"])
            if pid_int in known:
                continue
            img_rel = await asyncio.to_thread(self._download, meta)
            if not img_rel:
                continue

            draft = await self._compose(meta, self.img_dir / Path(img_rel).name)
            pid = f"ph_{pid_int}"
            rarity = next_rarity(counts, self.cfg.rarity_weights)
            existing.setdefault("pigs", []).append(
                {
                    "pid": pid,
                    "title": meta["title"],
                    "tag": draft.tag,
                    "persona": draft.persona,
                    "rarity": rarity,
                    "img": img_rel,
                    "origin": "dynamic",
                    "pighub_id": pid_int,
                }
            )
            counts[rarity] = counts.get(rarity, 0) + 1
            known.add(pid_int)
            added += 1
            # 逐只落盘：中途重启或抛错时，已完成的进度不会丢
            existing["schema"] = 2
            existing["built_at"] = time.strftime("%Y-%m-%d")
            self._save_store(existing)
        if added:
            self._mark_sync()
        return added

    # ── 拉列表 ──────────────────────────────────────────────
    def _fetch_new(self, limit: int) -> list[dict]:
        resp = requests.get(API_URL, headers=HEADERS, timeout=self.cfg.request_timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"PigHub 返回异常：{payload.get('message')}")

        have = {p.pighub_id for p in self.catalog.all if p.pighub_id is not None}
        have |= {int(e.get("pighub_id", 0)) for e in self._load_store().get("pigs", [])}

        cand = [p for p in payload.get("data", []) if int(p.get("id", 0)) not in have]
        cand.sort(
            key=lambda x: (int(x.get("view_count", 0)), int(x.get("download_count", 0))),
            reverse=True,
        )
        return [
            {
                "id": int(p["id"]),
                "title": (p.get("title") or "").strip() or f"猪猪{p['id']}",
                "url": IMAGE_BASE + (p.get("image_url") or ""),
                "gif": str(p.get("filename", "")).lower().endswith(".gif"),
            }
            for p in cand[: max(limit * 3, 10)]
        ]

    # ── 下载压缩 ────────────────────────────────────────────
    def _download(self, meta: dict) -> str | None:
        self.img_dir.mkdir(parents=True, exist_ok=True)
        target = self.img_dir / f"{meta['id']}.webp"
        if target.exists() and target.stat().st_size <= MAX_BYTES:
            return f"images/{target.name}"
        try:
            resp = requests.get(meta["url"], headers=HEADERS, timeout=self.cfg.request_timeout)
            resp.raise_for_status()
            if len(resp.content) > 25 * 1024 * 1024:
                logger.warning(f"[每日小猪] 图片过大，跳过 {meta['id']}")
                return None
            data = _compress(resp.content, meta["gif"])
            target.write_bytes(data)
            return f"images/{target.name}"
        except Exception as exc:
            logger.warning(f"[每日小猪] 下载失败 {meta['id']}：{type(exc).__name__}: {exc}")
            return None

    # ── 文案 ────────────────────────────────────────────────
    async def _compose(self, meta: dict, img_path: Path) -> Draft:
        if self.cfg.use_llm_persona:
            provider = await self._provider()
            if provider is not None:
                # 不迷信模型的 modalities 声明：deepseek-flash 这类新版模型
                # 实际能读图但没标 image，所以先带图试，失败自动退纯文本。
                use_img = self.cfg.allow_vision and not self._no_image
                draft = await self._ask_llm(provider, meta["title"], img_path if use_img else None)
                if draft is not None:
                    return draft
        logger.info(f"[每日小猪] 「{meta['title']}」使用内置模板文案")
        return _from_template(meta["title"])

    async def _provider(self, quiet: bool = False):
        if self._provider_inst is not None:
            return self._provider_inst
        try:
            from astrbot.core.provider.entities import ProviderType

            provider = await self.context.provider_manager.get_using_provider_async(
                ProviderType.CHAT_COMPLETION
            )
        except Exception as exc:
            if not quiet:
                logger.warning(f"[每日小猪] 获取对话模型失败：{type(exc).__name__}: {exc}")
            return None
        if provider is None:
            if not quiet and not self._provider_warned:
                logger.warning(
                    "[每日小猪] 当前没有可用的对话模型，扩充素材只能用内置模板。"
                    "请到 AstrBot 面板确认默认对话模型已启用"
                )
                self._provider_warned = True
            return None
        self._provider_inst = provider
        return provider

    async def _await_provider(self, timeout: float = 300.0) -> None:
        """等模型初始化完成。AstrBot 的模型加载发生在插件 initialize 之后。"""
        if not self.cfg.use_llm_persona:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self._provider(quiet=True) is not None:
                logger.info("[每日小猪] 对话模型已就绪，扩充素材使用模型生成文案")
                self._provider_warned = False
                return
            await asyncio.sleep(3)
        logger.warning("[每日小猪] 等待对话模型超时，本轮扩充改用内置模板")

    async def _ask_llm(self, provider, title: str, img_path: Path | None) -> Draft | None:
        prompt = _build_prompt(title, with_image=img_path is not None)
        images: list[str] = []
        if img_path is not None:
            try:
                raw = img_path.read_bytes()
                images = ["data:image/webp;base64," + base64.b64encode(raw).decode()]
            except OSError as exc:
                logger.warning(f"[每日小猪] 读取图片失败，转纯文本：{exc}")
                images = []

        for attempt in (1, 2):
            try:
                resp = await provider.text_chat(prompt=prompt, image_urls=images or None)
                text = getattr(resp, "completion_text", "") or ""
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    self._add_usage(
                        int(getattr(usage, "input_other", 0) or 0),
                        int(getattr(usage, "input_cached", 0) or 0),
                        int(getattr(usage, "output", 0) or 0),
                    )
            except Exception as exc:
                if images:          # 模型不认图，退到纯文本再试一次
                    logger.info(
                        f"[每日小猪] 模型未能读图（{type(exc).__name__}: {exc}），改用纯文本重试"
                    )
                    self._no_image = True
                    images = []
                    continue
                logger.warning(
                    f"[每日小猪] 模型调用失败：{type(exc).__name__}: {exc}"
                )
                self._provider_inst = None      # 可能已被重载或失效，下次重新获取
                return None

            draft = _parse_draft(text)
            if draft is not None:
                return draft
            logger.info(f"[每日小猪] 模型输出不合格式（第 {attempt} 次）：{text[:60]!r}")
            if attempt == 2:
                return None
        return None

    # ── 存储 ────────────────────────────────────────────────
    def _load_store(self) -> dict:
        if not self.store.exists():
            return {"schema": 2, "pigs": []}
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("pigs"), list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return {"schema": 2, "pigs": []}

    def _save_store(self, data: dict) -> None:
        self.dynamic_dir.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── token 用量 ──────────────────────────────────────────
    def _load_usage(self) -> dict:
        base = {"input_other": 0, "input_cached": 0, "output": 0, "calls": 0}
        if not self.usage_path.exists():
            return base
        try:
            data = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return base
        if not isinstance(data, dict):
            return base
        for key in base:
            try:
                base[key] = max(0, int(data.get(key, 0)))
            except (TypeError, ValueError):
                pass
        return base

    def _add_usage(self, input_other: int, input_cached: int, output: int) -> None:
        self._usage["input_other"] += max(0, input_other)
        self._usage["input_cached"] += max(0, input_cached)
        self._usage["output"] += max(0, output)
        self._usage["calls"] += 1
        try:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            self.usage_path.write_text(
                json.dumps(self._usage, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    @property
    def usage(self) -> dict:
        return dict(self._usage)


# ── 纯函数 ──────────────────────────────────────────────────
def _compress(raw: bytes, is_gif: bool) -> bytes:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        if is_gif and len(raw) > MAX_BYTES:
            image.seek(0)
        image = image.convert("RGBA")
        w, h = image.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        for q in (WEBP_Q, 70, 60, 50):
            buf = io.BytesIO()
            image.save(buf, "WEBP", quality=q, method=6)
            if buf.tell() <= MAX_BYTES:
                return buf.getvalue()
        return buf.getvalue()
    except Exception:
        return raw


def _build_prompt(title: str, with_image: bool) -> str:
    vision_hint = "下面的图片就是这只小猪，请先看图再写。" if with_image else ""
    return (
        "你在给一只表情包小猪写卡片文案，风格要求：沙雕、有梗、不煽情、不说教，"
        "严禁出现人生道理或正能量总结。\n"
        f"这只猪的标题是「{title}」。{vision_hint}\n"
        "请严格输出一个 JSON 对象，不要输出任何其他内容：\n"
        '{"tag": "6-12字的一句钩子", "persona": "60-110字的吐槽，必须有一个明确的梗"}'
    )


def _parse_draft(text: str) -> Draft | None:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    tag = str(payload.get("tag") or "").strip()
    persona = str(payload.get("persona") or "").strip()
    if not (4 <= len(tag) <= 16):
        return None
    if not (40 <= len(persona) <= 130):
        return None
    return Draft(tag=tag, persona=persona)


def _from_template(title: str) -> Draft:
    """没有可用模型时的兜底文案。用 sha1 稳定取模，避免标题字和相同导致撞车。"""
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()
    tag = _TAG_POOL[int(digest[8:16], 16) % len(_TAG_POOL)]
    persona = _PERSONA_POOL[int(digest[24:32], 16) % len(_PERSONA_POOL)].format(t=title)
    return Draft(tag=tag, persona=persona)
