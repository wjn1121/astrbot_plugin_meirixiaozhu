# -*- coding: utf-8 -*-
"""
字体子集化 —— 构建期脚本，不随插件运行。

分级策略（体积与覆盖的平衡）：
- 标题字体（庆科黄油体）：ASCII + GB2312 一级字库 + 标点
  猪名通常 2-6 个字，一级字库覆盖极充分；若出现生僻字，运行时整行回退到正文字体。
- 正文字体（霞鹜漫黑）：ASCII + GB2312 全集 + 标点 + 额外符号
  性格解析是长文本，且联网扩充的文案由模型现场生成，字符不可预知，必须留足余量。

用法：
    python tools/build_fonts.py <字体源目录> <输出目录>
"""
import json
import shutil
import sys
from pathlib import Path

from fontTools import subset

BASE_DIR = Path(__file__).resolve().parent.parent

# GB2312 之外仍可能用到的符号（弯引号是最容易踩的坑，它不在 GB2312 里）
EXTRA = (
    "—…·～￥「」『』【】《》〈〉？！，。；：、（）"
    "“”‘’＂＇　"                     # 弯引号 / 全角空格
    "①②③④⑤⑥⑦⑧⑨⑩★☆♦◆●○□■△▲※→←↑↓"
    "×÷±≤≥≠∞℃§¶†‡•‰′″"
)

# 现有文案里出现的字符必须全部覆盖，否则会渲染成方块
CATALOG_JSON = "resource/pigs.json"

SOURCES = [
    # (源文件名, 输出文件名, 字符集级别)
    ("ZCOOLQingKeHuangYou-Regular.ttf", "qingke-huangyou.ttf", "level1"),
    ("LXGWMarkerGothic-Regular.ttf", "marker-gothic.ttf", "full"),
]

# 子集字体实际覆盖的字符清单，运行期据此判断缺字并回退
TITLE_CHARSET = "charset-title.txt"
BODY_CHARSET = "charset-body.txt"

# 随字体一起分发的许可文本（OFL-1.1 要求）
LICENSES = [
    ("OFL-QingKeHuangYou.txt", "OFL-QingKeHuangYou.txt"),
    ("OFL-MarkerGothic.txt", "OFL-MarkerGothic.txt"),
]


def gb2312_hanzi(level: str) -> set[str]:
    """GB2312 汉字：level1 取一级常用字，full 取一级 + 二级。"""
    hi_range = range(0xB0, 0xD8) if level == "level1" else range(0xB0, 0xF8)
    chars: set[str] = set()
    for hi in hi_range:
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= ch <= "\u9fff":
                chars.add(ch)
    return chars


def catalog_chars(limit: int | None = None) -> set[str]:
    """把现有猪库文案里出现的字符全部收进来，保证内置内容一定不缺字。"""
    path = BASE_DIR / CATALOG_JSON
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    chars: set[str] = set()
    for pig in doc.get("pigs") or []:
        for key in ("title", "tag", "persona"):
            chars |= set(str(pig.get(key) or ""))
    return chars


def build_charset(level: str) -> str:
    chars = {chr(c) for c in range(0x20, 0x7F)}          # ASCII 可打印
    chars |= gb2312_hanzi(level)
    chars |= set(EXTRA)
    chars |= catalog_chars()                             # 现有文案用到的字全保留
    return "".join(sorted(chars))


def subset_one(src: Path, dst: Path, text: str) -> tuple[int, int]:
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]          # 保留版权等 name 记录（OFL 要求）
    opts.name_legacy = True
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.glyph_names = False
    opts.drop_tables += ["DSIG"]   # 子集化后签名失效
    opts.desubroutinize = True

    font = subset.load_font(str(src), opts)
    sub = subset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(font)
    subset.save_font(font, str(dst), opts)

    return src.stat().st_size, dst.stat().st_size


def dump_charset(ttf: Path, out: Path) -> int:
    """把子集字体实际覆盖的字符写成一行文本，供运行期做缺字判断。"""
    from fontTools.ttLib import TTFont

    font = TTFont(str(ttf), lazy=True)
    cmap = font.getBestCmap() or {}
    font.close()

    chars = []
    for cp in sorted(cmap):
        if cp < 0x20:
            continue
        try:
            chars.append(chr(cp))
        except (ValueError, OverflowError):
            continue
    out.write_text("".join(chars), encoding="utf-8")
    return len(chars)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    total_before = total_after = 0
    for src_name, dst_name, level in SOURCES:
        src = src_dir / src_name
        if not src.exists():
            print(f"[skip] {src} 不存在")
            continue
        text = build_charset(level)
        dst = out_dir / dst_name
        b, a = subset_one(src, dst, text)
        total_before += b
        total_after += a

        cs_name = TITLE_CHARSET if level == "level1" else BODY_CHARSET
        n = dump_charset(dst, out_dir / cs_name)
        kb = (out_dir / cs_name).stat().st_size / 1024
        print(
            f"[ok] {dst_name:24s} 字符 {n:5d}  {b/1048576:6.2f}MB → {a/1048576:5.2f}MB  "
            f"({a/b*100:.0f}%)  清单 {cs_name} {kb:.0f}KB"
        )

    print(f"[total] {total_before/1048576:.2f}MB → {total_after/1048576:.2f}MB")

    # 许可文本随字体分发
    lic_dir = src_dir.parent / "ofl"
    for src_name, dst_name in LICENSES:
        s = lic_dir / src_name
        if s.exists():
            shutil.copy2(s, out_dir / dst_name)
            print(f"[license] {dst_name}")
        else:
            print(f"[warn] 缺少许可文本 {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
