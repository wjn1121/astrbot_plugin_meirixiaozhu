# -*- coding: utf-8 -*-
"""
sync_pighub.py — 一次性数据准备脚本（不随插件热加载运行）。

职责：
  1. 拉取 PigHub 全部小猪列表（GET /api/images?sort=0）。
  2. 按热度（view_count 降序，并列按 download_count 降序）取前 TOP_N（默认 100）只。
  3. 逐张下载图片，压缩后缓存到 resource/pighub/：
       - 静态图：等比缩放（超过 MAX_DIM 时），统一保存为 WebP（quality=82，保留透明），
         若 WebP 体积反而更大则保留原图字节。
       - 动图(GIF)：保留原动图字节（避免转 WebP 丢帧率）。
  4. 合并本地 resource/pig.json 与这 TOP_N 只，写入 resource/pig_extended.json。
       - PigHub 条的 description/analysis 预置为空（需人工按图填写）。

用法：python sync_pighub.py
"""
import io
import json
from pathlib import Path

import requests
from PIL import Image

BASE_DIR = Path(__file__).parent
RES_DIR = BASE_DIR / "resource"
CACHE_DIR = RES_DIR / "pighub"
LOCAL_JSON = RES_DIR / "pig.json"
EXTENDED_JSON = RES_DIR / "pig_extended.json"

API_URL = "https://www.pighub.top/api/images?sort=0"
IMAGE_BASE = "https://www.pighub.top"
TOP_N = 100
MAX_DIM = 512           # 超过此尺寸则等比缩小
WEBP_QUALITY = 82       # 清晰优先、体积尽量小
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_pig_list() -> list[dict]:
    """拉取并校验 PigHub 全量列表。"""
    resp = requests.get(API_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0 or not isinstance(data.get("data"), list):
        raise RuntimeError(f"PigHub 返回异常: {data.get('message')}")
    return data["data"]


def pick_top(pigs: list[dict], n: int = TOP_N) -> list[dict]:
    """按热度取前 n 只：view_count 降序，并列按 download_count 降序。"""
    return sorted(
        pigs,
        key=lambda x: (int(x.get("view_count", 0)), int(x.get("download_count", 0))),
        reverse=True,
    )[:n]


def download_image(url: str, raw_limit: int = 20 * 1024 * 1024) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    if len(resp.content) > raw_limit:
        raise RuntimeError(f"图片过大({len(resp.content)} bytes): {url}")
    return resp.content


def compress_image(raw: bytes, is_gif: bool) -> tuple[bytes, str]:
    """
    压缩图片。
    :return: (输出字节, 输出扩展名(不含点)) ；
             若压缩结果更大则回退到原图字节，并给出原格式扩展名。
    """
    if is_gif:
        # 动图保留动画：直接存原字节
        return raw, "gif"

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        orig_ext = (im.format or "png").lower()
        im = im.convert("RGBA")
        w, h = im.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
        webp_bytes = buf.getvalue()

        # 若 WebP 反而更大，回退原图字节
        if len(webp_bytes) < len(raw):
            return webp_bytes, "webp"
        return raw, orig_ext
    except Exception:
        # 解码失败：原样保留，交给插件兜底
        return raw, "png"


def image_path_for_local(pig_id: str) -> str:
    """返回本地基础猪的图片相对路径，无则空串。"""
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        f = RES_DIR / "image" / f"{pig_id}.{ext}"
        if f.exists():
            return str(f.relative_to(BASE_DIR)).replace("\\", "/")
    return ""


def build_extended() -> int:
    pigs = fetch_pig_list()
    top = pick_top(pigs)
    print(f"[sync] PigHub 总数 {len(pigs)}，热度前 {len(top)} 只")
    top.sort(key=lambda x: int(x.get("view_count", 0)), reverse=True)

    # 1) 本地基础猪
    local_data = json.loads(LOCAL_JSON.read_text(encoding="utf-8"))
    extended: list[dict] = []
    for item in local_data:
        pid = item.get("id", "")
        extended.append({
            "id": pid,
            "name": item.get("name", "未知小猪"),
            "description": item.get("description", ""),
            "analysis": item.get("analysis", ""),
            "image": image_path_for_local(pid),
            "source": "local",
            "pighub_id": None,
        })

    # 2) PigHub 热度前 TOP_N
    # 若已存在 pig_extended.json，保留其上人工编写的文案（description/analysis），避免重复运行时丢失
    existing_copy: dict[str, dict] = {}
    if EXTENDED_JSON.exists():
        try:
            for e in json.loads(EXTENDED_JSON.read_text(encoding="utf-8")):
                if e.get("source") == "pighub" and e.get("description"):
                    existing_copy[str(e.get("pighub_id"))] = {
                        "description": e.get("description", ""),
                        "analysis": e.get("analysis", ""),
                    }
        except Exception:
            existing_copy = {}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for p in top:
        pid = p.get("id")
        title = (p.get("title") or "").strip() or f"猪猪{pid}"
        is_gif = str(p.get("filename", "")).lower().endswith(".gif")
        img_rel = f"https://www.pighub.top{p.get('image_url', '')}" if False else ""
        url = IMAGE_BASE + p.get("image_url", "")
        try:
            raw = download_image(url)
            out_bytes, ext = compress_image(raw, is_gif)
            out_rel = CACHE_DIR / f"{pid}.{ext}"
            out_rel.write_bytes(out_bytes)
            image_ref = str(out_rel.relative_to(BASE_DIR)).replace("\\", "/")
            prev = existing_copy.get(str(pid), {})
            extended.append({
                "id": f"pighub_{pid}",
                "name": title,
                "description": prev.get("description", ""),   # 保留人工文案，没有则留空
                "analysis": prev.get("analysis", ""),         # 保留人工文案，没有则留空
                "image": image_ref,
                "source": "pighub",
                "pighub_id": pid,
            })
            ok += 1
        except Exception as e:
            print(f"[sync] 跳过 pid={pid} ({title}): {e}")

    EXTENDED_JSON.write_text(
        json.dumps(extended, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[sync] 写入 {EXTENDED_JSON}，共 {len(extended)} 条（PigHub 成功 {ok} 条）")
    return ok


if __name__ == "__main__":
    n = build_extended()
    print(f"[sync] 完成，PigHub 成功下载并压缩 {n} 张图片")
