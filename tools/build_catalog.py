# -*- coding: utf-8 -*-
"""
小猪素材构建 —— 构建期脚本，不随插件运行。

职责：
  1. 复用已有文案的 PigHub 小猪（保留已有的 tag / persona）。
  2. 从 PigHub 按热度补充新小猪，直到池子达到目标数量。
  3. 统一压缩图片到 images/：最大边 640px、WebP q=82、单图 ≤180KB。
  4. 生成 schema v2 的 pigs.json，并预计算每只猪的稀有度。

用法：
    python tools/build_catalog.py [目标数量]
"""
import io
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from meirixiaozhu.catalog import rarity_of  # noqa: E402

RES = BASE / "resource"
IMG_DIR = RES / "images"
PIGS_JSON = RES / "pigs.json"

API_URL = "https://www.pighub.top/api/images?sort=0"
IMAGE_BASE = "https://www.pighub.top"
HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_DIM = 640
WEBP_Q = 82
MAX_BYTES = 180 * 1024
TARGET = 150


def compress(raw: bytes, is_gif: bool) -> tuple[bytes, str]:
    """压缩到 WebP；GIF 超限时只取首帧。返回 (字节, 扩展名)。"""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if is_gif and len(raw) > MAX_BYTES:
            im.seek(0)                      # 只留首帧
        im = im.convert("RGBA")
        w, h = im.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        for q in (WEBP_Q, 70, 60, 50):
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=q, method=6)
            data = buf.getvalue()
            if len(data) <= MAX_BYTES:
                return data, "webp"
        return data, "webp"
    except Exception:
        return raw, "gif" if is_gif else "png"


def existing_with_text() -> list[dict]:
    """读取现有 pigs.json 中已有文案的小猪，重跑时据以保留文案不丢。"""
    p = PIGS_JSON
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for e in doc.get("pigs") or []:
        if (e.get("tag") or "").strip() and e.get("pighub_id"):
            out.append(e)
    return out


def fetch_list() -> list[dict]:
    r = requests.get(API_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0 or not isinstance(data.get("data"), list):
        raise RuntimeError(f"PigHub 返回异常: {data.get('message')}")
    return data["data"]


def pick_new(all_pigs: list[dict], taken: set[int], n: int) -> list[dict]:
    """按热度取新猪，跳过已入库的。"""
    cand = [p for p in all_pigs if int(p.get("id", 0)) not in taken]
    cand.sort(key=lambda x: (int(x.get("view_count", 0)), int(x.get("download_count", 0))), reverse=True)
    return cand[:n]


def save_img(pig_id: int, url: str, is_gif: bool) -> str | None:
    """下载并压缩单张图，返回相对路径。已有合规缓存则直接复用。"""
    for ext in ("webp", "gif", "png"):
        cached = IMG_DIR / f"{pig_id}.{ext}"
        if cached.exists() and cached.stat().st_size <= MAX_BYTES:
            return f"images/{pig_id}.{ext}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=40)
        r.raise_for_status()
        if len(r.content) > 25 * 1024 * 1024:
            raise RuntimeError("图片过大")
        data, ext = compress(r.content, is_gif)
        (IMG_DIR / f"{pig_id}.{ext}").write_bytes(data)
        return f"images/{pig_id}.{ext}"
    except Exception as e:
        print(f"   [skip] {pig_id}: {e}")
        return None


def main() -> int:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else TARGET
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    old = existing_with_text()
    print(f"[1/4] 已有文案的小猪：{len(old)} 只")

    all_pigs = fetch_list()
    print(f"[2/4] PigHub 可用总数：{len(all_pigs)} 只")

    taken = {int(e.get("pighub_id")) for e in old if e.get("pighub_id")}
    need = max(0, target - len(old))
    picks = pick_new(all_pigs, taken, need)
    print(f"[3/4] 需补充 {need} 只，实际候选 {len(picks)} 只")

    pigs: list[dict] = []

    # 已有小猪：迁移图片 + 保留文案
    for e in old:
        pid_int = int(e["pighub_id"])
        rel = None
        # img 字段形如 "images/1290.webp"，相对 resource/
        local = RES / (e.get("img") or "")
        if local.exists():
            data, ext = compress(local.read_bytes(), local.suffix.lower() == ".gif")
            (IMG_DIR / f"{pid_int}.{ext}").write_bytes(data)
            rel = f"images/{pid_int}.{ext}"
        else:
            print(f"   [warn] 本地图片缺失: {local}")
        if not rel:
            continue
        pid = f"ph_{pid_int}"
        pigs.append({
            "pid": pid,
            "title": e.get("title", ""),
            "tag": e.get("tag", ""),
            "persona": e.get("persona", ""),
            "rarity": rarity_of(pid),
            "img": rel,
            "origin": "pighub",
            "pighub_id": pid_int,
        })

    # 新增小猪：下载图片，文案留空待填写
    new_count = 0
    for p in picks:
        pid_int = int(p["id"])
        title = (p.get("title") or "").strip() or f"猪猪{pid_int}"
        is_gif = str(p.get("filename", "")).lower().endswith(".gif")
        rel = save_img(pid_int, IMAGE_BASE + p.get("image_url", ""), is_gif)
        if not rel:
            continue
        pid = f"ph_{pid_int}"
        pigs.append({
            "pid": pid,
            "title": title,
            "tag": "",
            "persona": "",
            "rarity": rarity_of(pid),
            "img": rel,
            "origin": "pighub",
            "pighub_id": pid_int,
        })
        new_count += 1

    doc = {
        "schema": 2,
        "built_at": time.strftime("%Y-%m-%d"),
        "pigs": pigs,
    }
    PIGS_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    rc = Counter(x["rarity"] for x in pigs)
    empty = sum(1 for x in pigs if not x["tag"])
    total_img = sum(f.stat().st_size for f in IMG_DIR.iterdir() if f.is_file())
    print(f"[4/4] 写入 {PIGS_JSON}")
    print(f"      共 {len(pigs)} 只（新增 {new_count}），待写文案 {empty} 只")
    print(f"      稀有度: " + "  ".join(f"{k}={rc[k]}" for k in ("N", "R", "SR", "SSR")))
    print(f"      图片 {len(list(IMG_DIR.iterdir()))} 张，合计 {total_img/1048576:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
