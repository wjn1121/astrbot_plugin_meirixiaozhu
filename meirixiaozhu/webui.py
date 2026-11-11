# -*- coding: utf-8 -*-
"""WebUI 控制台后端：面板页面的所有接口都挂在 /panel 下。

视图入口是 views/panel/index.html，前端通过 AstrBotPluginView bridge
调用这里的接口。面板自带配置读写、扩充猪池、数据管理三块能力。
"""

from __future__ import annotations

import base64
import inspect
import json
import shutil
import time
from pathlib import Path

from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_meirixiaozhu"
API = f"/{PLUGIN_NAME}/panel"

# 允许在面板里热改的键，必须同时出现在 _conf_schema.json 里
_EDITABLE = frozenset(
    {
        "daily_draw_count",
        "at_view_pig",
        "need_at",
        "trigger_keywords",
        "help_keywords",
        "codex_keywords",
        "rank_keywords",
        "stats_keywords",
        "sync_keywords",
        "rarity_enabled",
        "pity_enabled",
        "pity_threshold",
        "rarity_weight_n",
        "rarity_weight_r",
        "rarity_weight_sr",
        "codex_enabled",
        "rank_enabled",
        "rank_size",
        "card_frame",
        "auto_sync",
        "sync_batch_size",
        "sync_interval_hours",
        "max_pool_size",
        "use_llm_persona",
        "allow_vision",
        "request_timeout",
        "price_input_usd",
        "price_output_usd",
        "usd_cny",
        "debug_log",
    }
)

# 存储分区的展示顺序与用途说明
_ZONES = (
    ("ledger", "SQLite 账本", "抽取记录、每日次数、图鉴数据与保底计数"),
    ("names", "自定义命名", "在面板里改过的卡片名字"),
    ("texts", "文案覆盖", "在面板里改过的一句话与描述"),
    ("usage", "Token 用量记录", "扩充素材累计消耗的 token"),
    ("dynamic_json", "JSON 业务记录", "运行时从 PigHub 扩充的小猪文案"),
    ("dynamic_img", "动态图片缓存", "扩充小猪的本地压缩图，可随时清理"),
    ("builtin_img", "内置小猪图片", "随插件分发的基础素材"),
    ("fonts", "字体文件", "卡面渲染字体与 OFL 许可文本"),
    ("other", "其他文件", "代码、配置与文档"),
)


def _dir_stats(path: Path) -> tuple[int, int]:
    """返回 (文件数, 总字节数)。"""
    if not path.exists():
        return 0, 0
    if path.is_file():
        return 1, path.stat().st_size
    files = 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            total += item.stat().st_size
    return files, total


def _human(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num / 1048576:.2f} MB"


def _cost(usage: dict, cfg) -> tuple[float, float]:
    """按配置单价折算 (美元, 人民币)。输入按未命中缓存计，属于偏高的保守估计。"""
    tokens_in = int(usage.get("input_other", 0)) + int(usage.get("input_cached", 0))
    tokens_out = int(usage.get("output", 0))
    usd = tokens_in / 1_000_000 * cfg.price_input_usd + tokens_out / 1_000_000 * cfg.price_output_usd
    return round(usd, 6), round(usd * cfg.usd_cny, 4)


def _usage_view(usage: dict, cfg) -> dict:
    calls = max(1, int(usage.get("calls", 0)))
    usd, cny = _cost(usage, cfg)
    tokens_in = int(usage.get("input_other", 0)) + int(usage.get("input_cached", 0))
    return {
        "input_other": int(usage.get("input_other", 0)),
        "input_cached": int(usage.get("input_cached", 0)),
        "input_total": tokens_in,
        "output": int(usage.get("output", 0)),
        "calls": int(usage.get("calls", 0)),
        "input_per_call": round(tokens_in / calls),
        "output_per_call": round(int(usage.get("output", 0)) / calls),
        "usd": usd,
        "cny": cny,
        "per_call_cny": round(cny / calls, 5),
        "price_input_usd": cfg.price_input_usd,
        "price_output_usd": cfg.price_output_usd,
        "usd_cny": cfg.usd_cny,
    }


async def _payload() -> dict:
    data = await request.json({})
    return data if isinstance(data, dict) else {}


class WebConsole:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._expanding = False

    # ── 注册 ────────────────────────────────────────────────
    def register(self, context) -> None:
        routes = (
            ("/storage", self.storage, ["GET"], "存储占用概览"),
            ("/preview", self.preview, ["GET"], "卡面预览"),
            ("/cards", self.cards, ["GET"], "卡片列表"),
            ("/rename", self.rename, ["POST"], "修改卡片名字"),
            ("/text", self.text, ["POST"], "修改卡片文案"),
            ("/config", self.config, ["GET", "POST"], "读写插件配置"),
            ("/expand", self.expand, ["POST"], "手动扩充猪池"),
            ("/usage", self.usage, ["GET"], "Token 用量与费用"),
            ("/data", self.data, ["GET", "POST"], "数据管理"),
        )
        for suffix, handler, methods, desc in routes:
            context.register_web_api(f"{API}{suffix}", handler, methods, desc)

    def _version(self) -> str:
        """版本号以 metadata.yaml 为准，避免面板显示和插件市场撞号。"""
        try:
            raw = (Path(self.plugin.plugin_dir) / "metadata.yaml").read_text(encoding="utf-8")
        except OSError:
            return ""
        for line in raw.splitlines():
            if line.strip().startswith("version:"):
                value = line.split(":", 1)[1].split("#", 1)[0]   # 去掉行尾注释
                return value.strip().strip("\"'")
        return ""

    # ── 存储占用 ────────────────────────────────────────────
    async def storage(self):
        plugin = self.plugin
        data_dir: Path = plugin.data_dir
        res_dir: Path = plugin.res_dir
        dynamic: Path = plugin.dynamic_dir

        raw = {
            "ledger": _dir_stats(data_dir / "ledger.sqlite3"),
            "names": _dir_stats(data_dir / "names.json"),
            "texts": _dir_stats(data_dir / "texts.json"),
            "usage": _dir_stats(data_dir / "usage.json"),
            "dynamic_json": _dir_stats(dynamic / "pigs.json"),
            "dynamic_img": _dir_stats(dynamic / "images"),
            "builtin_img": _dir_stats(res_dir / "images"),
            "fonts": _dir_stats(res_dir / "fonts"),
        }

        counted = sum(b for _, b in raw.values())
        total_data = _dir_stats(data_dir)[1] + _dir_stats(res_dir)[1]
        raw["other"] = (1 if total_data > counted else 0, max(0, total_data - counted))

        grand = sum(b for _, b in raw.values()) or 1
        zones = []
        for key, label, purpose in _ZONES:
            files, size = raw.get(key, (0, 0))
            zones.append(
                {
                    "key": key,
                    "label": label,
                    "files": files,
                    "bytes": size,
                    "size": _human(size),
                    "percent": round(size / grand * 100, 1),
                    "purpose": purpose,
                }
            )

        return json_response(
            {
                "version": self._version(),
                "total": _human(sum(b for _, b in raw.values())),
                "total_bytes": sum(b for _, b in raw.values()),
                "data_dir": str(data_dir),
                "zones": zones,
                "pool": {
                    "total": plugin.catalog.total,
                    "rarity": plugin.catalog.count_by_rarity(),
                    "weights": list(plugin.catalog.weights),
                },
                "ledger": plugin.ledger.stats(),
            }
        )

    # ── 卡面预览 ────────────────────────────────────────────
    async def preview(self):
        plugin = self.plugin
        rarity = (request.query.get("rarity", "SSR") or "SSR").upper()
        frame = request.query.get("frame", "rarity") or "rarity"
        pid = request.query.get("pid") or ""

        pig = plugin.catalog.get(pid) if pid else None
        if pig is None:
            pig = next((p for p in plugin.catalog.all if p.rarity == rarity), None)
        if pig is None:
            pig = next(iter(plugin.catalog.all), None)
        if pig is None:
            return error_response("猪圈是空的", status_code=404)

        try:
            png = plugin.renderer.render_png(pig, frame=frame)
        except Exception as exc:  # 渲染失败要能看见原因
            return error_response(f"渲染失败：{exc}", status_code=500)

        plain_title, plain_tag, plain_persona = plugin.catalog.default_text(pig.pid)
        return json_response(
            {
                "pid": pig.pid,
                "rarity": pig.rarity,
                "title": pig.title,
                "tag": pig.tag,
                "persona": pig.persona,
                "origin": pig.origin,
                "serial": f"No. {pig.pighub_id:04d}" if pig.pighub_id else "",
                "renamed": pig.pid in plugin.catalog.names,
                "edited": {
                    "title": pig.pid in plugin.catalog.names,
                    "tag": pig.tag != plain_tag,
                    "persona": pig.persona != plain_persona,
                },
                "defaults": {
                    "title": plain_title,
                    "tag": plain_tag,
                    "persona": plain_persona,
                },
                "bytes": len(png),
                "image": "data:image/png;base64," + base64.b64encode(png).decode(),
            }
        )

    # ── 卡片列表 / 改名 ─────────────────────────────────────
    async def cards(self):
        plugin = self.plugin
        keyword = (request.query.get("q", "") or "").strip().lower()
        try:
            limit = max(1, min(500, int(request.query.get("limit", "500") or 500)))
        except (TypeError, ValueError):
            limit = 500

        names = plugin.catalog.names
        items = []
        for pig in plugin.catalog.all:
            if keyword and keyword not in pig.title.lower() and keyword not in pig.pid.lower():
                continue
            items.append(
                {
                    "pid": pig.pid,
                    "title": pig.title,
                    "rarity": pig.rarity,
                    "origin": pig.origin,
                    "renamed": pig.pid in names,
                }
            )
            if len(items) >= limit:
                break
        return json_response({"total": plugin.catalog.total, "items": items})

    async def rename(self):
        body = await _payload()
        pid = str(body.get("pid") or "").strip()
        name = str(body.get("name") or "")
        if not pid:
            return error_response("缺少卡片编号", status_code=400)
        if not self.plugin.catalog.rename(pid, name):
            return error_response("没找到这只小猪", status_code=404)
        pig = self.plugin.catalog.get(pid)
        return json_response({"pid": pid, "title": pig.title if pig else ""})

    async def text(self):
        """改卡片文案：显示名 / 一句话 / 描述。空值表示恢复素材原文。"""
        plugin = self.plugin
        body = await _payload()
        pid = str(body.get("pid") or "").strip()
        if not pid:
            return error_response("缺少卡片编号", status_code=400)
        if plugin.catalog.get(pid) is None:
            return error_response("没找到这只小猪", status_code=404)

        fields = body.get("fields")
        if not isinstance(fields, dict):
            fields = {}

        changed: list[str] = []
        if "title" in fields and plugin.catalog.set_title(pid, str(fields.get("title") or "")):
            changed.append("title")
        for field in ("tag", "persona"):
            if field in fields and plugin.catalog.set_text(
                pid, field, str(fields.get(field) or "")
            ):
                changed.append(field)

        pig = plugin.catalog.get(pid)
        plain_title, plain_tag, plain_persona = plugin.catalog.default_text(pid)
        return json_response(
            {
                "pid": pid,
                "changed": changed,
                "title": pig.title if pig else "",
                "tag": pig.tag if pig else "",
                "persona": pig.persona if pig else "",
                "defaults": {
                    "title": plain_title,
                    "tag": plain_tag,
                    "persona": plain_persona,
                },
            }
        )

    # ── 配置读写 ────────────────────────────────────────────
    def _schema(self) -> dict:
        path = Path(self.plugin.plugin_dir) / "_conf_schema.json"
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def config(self):
        plugin = self.plugin
        schema = self._schema()

        if request.method.upper() == "POST":
            body = await _payload()
            values = body.get("values")
            if not isinstance(values, dict):
                return error_response("请求格式不对", status_code=400)

            changed = []
            for key, raw in values.items():
                if key not in _EDITABLE or key not in schema:
                    continue
                spec = schema[key] if isinstance(schema[key], dict) else {}
                kind = str(spec.get("type") or "string")
                try:
                    if kind == "bool":
                        val = bool(raw)
                    elif kind == "int":
                        val = int(raw)
                        low, high = spec.get("min"), spec.get("max")
                        if isinstance(low, (int, float)):
                            val = max(int(low), val)
                        if isinstance(high, (int, float)):
                            val = min(int(high), val)
                    elif kind == "float":
                        val = float(raw)
                    else:
                        val = str(raw)
                except (TypeError, ValueError):
                    continue
                if plugin.config.get(key) != val:
                    plugin.config[key] = val
                    changed.append(key)

            if changed:
                result = plugin.config.save_config()
                if inspect.isawaitable(result):
                    await result
                plugin.apply_config()
            return json_response({"changed": changed, "count": len(changed)})

        values = {}
        for key, spec in schema.items():
            values[key] = plugin.config.get(key, spec.get("default") if isinstance(spec, dict) else None)

        cfg = plugin._cfg()
        n, r, sr, ssr = cfg.rarity_weights
        return json_response(
            {
                "schema": schema,
                "values": values,
                "derived": {
                    "rarity_weights": [n, r, sr, ssr],
                    "rarity_ssr": ssr,
                    "rarity_total": n + r + sr + ssr,
                },
            }
        )

    # ── 扩充猪池 ────────────────────────────────────────────
    async def expand(self):
        plugin = self.plugin
        if self._expanding:
            return error_response("正在扩充中，请等这一轮完成", status_code=409)

        body = await _payload()
        try:
            count = int(body.get("count", 5))
        except (TypeError, ValueError):
            count = 5
        count = max(1, min(30, count))

        cfg = plugin._cfg()
        pool_size = plugin.catalog.total
        room = max(0, cfg.max_pool_size - pool_size)
        if room <= 0:
            return error_response(f"猪池已达上限 {cfg.max_pool_size}，先到数据管理里清理", status_code=400)

        from .sync import SyncWorker

        worker = SyncWorker(plugin.context, cfg, plugin.catalog, plugin.data_dir)
        before = worker.usage
        self._expanding = True
        started = time.time()
        try:
            added = await worker.sync_once(count)
        except Exception as exc:
            return error_response(f"扩充失败：{exc}", status_code=500)
        finally:
            self._expanding = False

        plugin.catalog.reload()
        after = worker.usage
        delta = {k: max(0, int(after.get(k, 0)) - int(before.get(k, 0))) for k in after}
        usd, cny = _cost(delta, cfg)
        return json_response(
            {
                "added": added,
                "requested": count,
                "pool": plugin.catalog.total,
                "elapsed": round(time.time() - started, 1),
                "usage": _usage_view(delta, cfg),
                "cost_usd": usd,
                "cost_cny": cny,
            }
        )

    async def usage(self):
        plugin = self.plugin
        cfg = plugin._cfg()
        from .sync import SyncWorker

        worker = SyncWorker(plugin.context, cfg, plugin.catalog, plugin.data_dir)
        return json_response(_usage_view(worker.usage, cfg))

    # ── 数据管理 ────────────────────────────────────────────
    async def data(self):
        plugin = self.plugin
        dynamic = plugin.dynamic_dir
        store = dynamic / "pigs.json"
        cfg = plugin._cfg()

        if request.method.upper() == "POST":
            body = await _payload()
            action = str(body.get("action") or "")

            if action == "clean_dynamic":
                shutil.rmtree(dynamic / "images", ignore_errors=True)
                if store.exists():
                    store.unlink()
                plugin.catalog.reload()
                return json_response({"action": action, "pool": plugin.catalog.total})

            if action == "delete_pig":
                pid = str(body.get("pid") or "").strip()
                removed = self._drop_dynamic(pid)
                plugin.catalog.reload()
                return json_response({"action": action, "removed": removed, "pool": plugin.catalog.total})

            if action == "reset_ledger":
                rows = plugin.ledger.wipe("all")
                return json_response({"action": action, "removed": rows})

            if action == "reset_pity":
                rows = plugin.ledger.wipe("pity")
                return json_response({"action": action, "removed": rows})

            if action == "reset_names":
                count = len(plugin.catalog.names)
                for pid in list(plugin.catalog.names):
                    plugin.catalog.rename(pid, "")
                return json_response({"action": action, "removed": count})

            if action == "reset_texts":
                return json_response(
                    {"action": action, "removed": plugin.catalog.clear_texts()}
                )

            if action == "clean_orphan":
                removed = self._drop_orphan_images()
                return json_response({"action": action, "removed": removed})

            return error_response(f"不认识的操作：{action}", status_code=400)

        dyn_pigs = 0
        if store.exists():
            try:
                dyn_pigs = len(json.loads(store.read_text(encoding="utf-8")).get("pigs") or [])
            except (json.JSONDecodeError, OSError):
                dyn_pigs = 0

        return json_response(
            {
                "pool": plugin.catalog.total,
                "dynamic_pigs": dyn_pigs,
                "dynamic_images": _dir_stats(dynamic / "images")[0],
                "dynamic_bytes": _human(_dir_stats(dynamic / "images")[1]),
                "orphan_images": self._count_orphan_images(),
                "names": len(plugin.catalog.names),
                "texts": len(plugin.catalog.texts),
                "ledger": plugin.ledger.stats(),
                "limits": {
                    "max_pool_size": cfg.max_pool_size,
                    "room": max(0, cfg.max_pool_size - plugin.catalog.total),
                },
            }
        )

    def _used_images(self) -> set[str]:
        store = self.plugin.dynamic_dir / "pigs.json"
        if not store.exists():
            return set()
        try:
            doc = json.loads(store.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        return {str(item.get("img") or "") for item in (doc.get("pigs") or [])}

    def _count_orphan_images(self) -> int:
        """统计没有被任何小猪引用的图片，多为同步中途失败留下的。"""
        img_dir = self.plugin.dynamic_dir / "images"
        if not img_dir.is_dir():
            return 0
        used = self._used_images()
        return sum(1 for f in img_dir.iterdir() if f.is_file() and f"images/{f.name}" not in used)

    def _drop_orphan_images(self) -> int:
        img_dir = self.plugin.dynamic_dir / "images"
        if not img_dir.is_dir():
            return 0
        used = self._used_images()
        removed = 0
        for f in img_dir.iterdir():
            if f.is_file() and f"images/{f.name}" not in used:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def _drop_dynamic(self, pid: str) -> int:
        """从动态猪库里删掉一只，连同它的图片。返回删除条数。"""
        if not pid.startswith("ph_"):
            return 0
        store = self.plugin.dynamic_dir / "pigs.json"
        if not store.exists():
            return 0
        try:
            doc = json.loads(store.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        pigs = doc.get("pigs") or []
        keep = []
        removed = 0
        for item in pigs:
            if str(item.get("pid")) == pid:
                img = str(item.get("img") or "")
                if img:
                    target = (self.plugin.dynamic_dir / img).resolve()
                    try:
                        target.relative_to(self.plugin.dynamic_dir.resolve())
                        target.unlink(missing_ok=True)
                    except (ValueError, OSError):
                        pass
                removed += 1
                continue
            keep.append(item)
        if removed:
            doc["pigs"] = keep
            store.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return removed
