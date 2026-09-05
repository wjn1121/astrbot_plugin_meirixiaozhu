import asyncio
import datetime
import json
import random
import tempfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import At

# 修复导入冲突：PIL 的 Image 重命名为 PILImage
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont


class MeiRiXiaoZhuPlugin(Star):
    CANVAS_WIDTH = 800
    CANVAS_HEIGHT = 800
    AVATAR_SIZE = 280
    SPACING_AVATAR_NAME = 20
    SPACING_NAME_DESC = 25
    SPACING_DESC_ANALYSIS = 30
    DESC_FONT_SIZE = 32
    ANALYSIS_FONT_SIZE = 28
    ANALYSIS_LINE_HEIGHT_FACTOR = 1.6
    ANALYSIS_WIDTH_RATIO = 0.85
    NAME_FONT_SIZE = 66

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.admins_id: list[str] = context.get_config().get("admins_id", []) or []
        self.at_view_pig: bool = self.config.get("at_view_pig", False)
        # 每人每日可抽取次数（WebUI 数字框），默认为 1
        self.daily_draw_count: int = max(1, int(self.config.get("daily_draw_count", 1)))

        self.plugin_dir = Path(__file__).parent
        self.plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_meirixiaozhu")
        self.res_dir = self.plugin_dir / "resource"
        self.font_dir = self.res_dir / "font"
        self.ext_json = self.res_dir / "pig_extended.json"
        self.local_json = self.res_dir / "pig.json"
        self.today_path = self.plugin_data_dir / "meirixiaozhu_daily.json"

        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.font_dir.mkdir(parents=True, exist_ok=True)

        # 小猪池：优先合并后的 pig_extended.json，缺省回退 pig.json
        self.pig_list = self._load_pig_list()

        self.font_regular = self._init_regular_font()  # 常规字体（描述/解析）
        self.font_bold = self._init_bold_font()  # 加粗字体（名称）

    def _load_pig_list(self) -> list[dict]:
        """
        加载小猪池。优先 read pig_extended.json（基础 + PigHub 前100）；
        PigHub 拉取不可用时回退到本地 pig.json。
        """
        if self.ext_json.exists():
            try:
                data = json.loads(self.ext_json.read_text("utf-8"))
                if isinstance(data, list) and data:
                    logger.info(f"已加载小猪池 {len(data)} 条（含 PigHub 扩充）")
                    return data
            except json.JSONDecodeError as e:
                logger.error(f"pig_extended.json 解析失败，回退 pig.json：{e}")
        return self.load_json(self.local_json, [])

    def _load_font(self, font_candidates, size, purpose):
        for font_path in font_candidates:
            if Path(font_path).exists():
                try:
                    return ImageFont.truetype(str(font_path), size)
                except Exception as e:
                    logger.warning(f"加载{purpose}字体{font_path}失败：{e}")
                    continue
        logger.warning(f"未找到{purpose}字体，使用默认字体")
        return ImageFont.load_default()

    def _init_regular_font(self):
        font_paths = [
            self.font_dir / "可爱字体.ttf",
            self.font_dir / "SourceHanSansCN-Regular.otf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        return self._load_font(font_paths, self.DESC_FONT_SIZE, "常规")

    def _init_bold_font(self):
        font_paths = [
            self.font_dir / "荆南麦圆体.otf",
            self.font_dir / "SourceHanSansCN-Bold.otf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        return self._load_font(font_paths, self.NAME_FONT_SIZE, "加粗")

    def _get_text_size(self, text: str, font):
        draw = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])
        except Exception:
            return draw.textsize(text, font=font)

    def _draw_bold_text(self, draw, pos, text, font, fill):
        x, y = pos
        offsets = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        for ox, oy in offsets:
            draw.text((x + ox, y + oy), text, fill=fill, font=font)
        draw.text((x, y), text, fill=fill, font=font)

    def load_json(self, path: Path, default):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return default
        try:
            return json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            logger.error(f"JSON文件解析失败，重置为默认值：{path}")
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return default

    def save_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def resolve_image(self, pig_data: dict) -> Path | None:
        """解析小猪图片的本地绝对路径，不存在则返回 None。"""
        img_rel = pig_data.get("image", "")
        if not img_rel:
            return None
        p = Path(self.plugin_dir, img_rel)
        if p.exists():
            return p
        # 兜底：尝试 resource/image/{id}.{ext}
        pid = pig_data.get("id", "")
        for ext in ("png", "jpg", "jpeg", "webp", "gif"):
            f = self.res_dir / "image" / f"{pid}.{ext}"
            if f.exists():
                return f
        return None

    def render_pig_image(self, pig_data: dict) -> Path | None:
        pig_name = pig_data.get("name", "未知小猪")
        pig_desc = pig_data.get("description", "无描述")
        pig_analysis = pig_data.get("analysis", "无解析") or "无解析"

        canvas_width = self.CANVAS_WIDTH
        canvas_height = self.CANVAS_HEIGHT
        canvas = PILImage.new("RGB", (canvas_width, canvas_height), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # 头像
        avatar_w, avatar_h = self.AVATAR_SIZE, self.AVATAR_SIZE
        avatar = None
        img_path = self.resolve_image(pig_data)
        if img_path:
            try:
                avatar = PILImage.open(img_path)
                avatar.thumbnail((avatar_w, avatar_h))
                if avatar.size != (avatar_w, avatar_h):
                    center_x = avatar.width // 2
                    center_y = avatar.height // 2
                    half = self.AVATAR_SIZE // 2
                    crop_box = (
                        center_x - half,
                        center_y - half,
                        center_x + half,
                        center_y + half,
                    )
                    avatar = avatar.crop(crop_box)
            except Exception as e:
                logger.error(f"加载小猪图片失败：{str(e)}")
                avatar = None

        # 名称
        name_font = self.font_bold
        name_w, name_h = self._get_text_size(pig_name, name_font)

        # 描述
        desc_font = self.font_regular.font_variant(size=self.DESC_FONT_SIZE)
        desc_w, desc_h = self._get_text_size(pig_desc, desc_font)

        # 解析（自动换行）
        analysis_font = self.font_regular.font_variant(size=self.ANALYSIS_FONT_SIZE)
        line_height = int(self.ANALYSIS_FONT_SIZE * self.ANALYSIS_LINE_HEIGHT_FACTOR)
        max_analysis_width = int(canvas_width * self.ANALYSIS_WIDTH_RATIO)
        analysis_lines = []
        current_line = ""
        for char in pig_analysis:
            current_line += char
            line_w, _ = self._get_text_size(current_line, analysis_font)
            if line_w > max_analysis_width:
                analysis_lines.append(current_line[:-1])
                current_line = char
        if current_line:
            analysis_lines.append(current_line)
        analysis_total_h = len(analysis_lines) * line_height

        # 总高 & 垂直居中
        total_content_h = (
            avatar_h
            + self.SPACING_AVATAR_NAME
            + name_h
            + self.SPACING_NAME_DESC
            + desc_h
            + self.SPACING_DESC_ANALYSIS
            + analysis_total_h
        )
        start_y = (canvas_height - total_content_h) // 2

        # 头像
        avatar_x = (canvas_width - avatar_w) // 2
        avatar_y = start_y
        if avatar:
            canvas.paste(avatar, (avatar_x, avatar_y), mask=avatar if avatar.mode == "RGBA" else None)
        else:
            error_font = self.font_regular.font_variant(size=24)
            error_text = "图片加载失败"
            error_w, _ = self._get_text_size(error_text, error_font)
            error_x = (canvas_width - error_w) // 2
            draw.text((error_x, avatar_y + 120), error_text, fill=(255, 0, 0), font=error_font)

        # 名称
        name_y = avatar_y + avatar_h + self.SPACING_AVATAR_NAME
        name_x = (canvas_width - name_w) // 2
        self._draw_bold_text(draw, (name_x, name_y), pig_name, name_font, (0, 0, 0))

        # 描述
        desc_y = name_y + name_h + self.SPACING_NAME_DESC
        desc_x = (canvas_width - desc_w) // 2
        draw.text((desc_x, desc_y), pig_desc, fill=(85, 85, 85), font=desc_font)

        # 解析
        analysis_y = desc_y + desc_h + self.SPACING_DESC_ANALYSIS
        for line in analysis_lines:
            line_w, _ = self._get_text_size(line, analysis_font)
            line_x = (canvas_width - line_w) // 2
            draw.text((line_x, analysis_y), line, fill=(51, 51, 51), font=analysis_font)
            analysis_y += line_height

        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                canvas.save(tmp_path, format="PNG", quality=95)
            return tmp_path if tmp_path.exists() else None
        except Exception as e:
            logger.error(f"合成图片失败：{str(e)}")
            return None

    def get_at_ids(self, event: AstrMessageEvent) -> list[str]:
        return [
            str(seg.qq)
            for seg in event.get_messages()
            if (isinstance(seg, At) and str(seg.qq) != event.get_self_id())
        ]

    def pick_pig(self, drawn_ids: set[str]) -> dict | None:
        """从池中随机选一只；优先避开当天已抽过的 id。"""
        candidates = self.pig_list or []
        if not candidates:
            return None
        fresh = [p for p in candidates if str(p.get("id", "")) not in drawn_ids]
        pool = fresh if fresh else candidates
        return random.choice(pool)

    @filter.command("每日小猪", alias={"抽小猪", "我的小猪", "meirixiaozhu"})
    async def roll_pig(self, event: AstrMessageEvent):
        today_str = datetime.date.today().isoformat()
        user_id = event.get_sender_id()

        # 艾特他人查看
        if self.at_view_pig:
            parts = event.message_str.strip().split()
            at_ids = self.get_at_ids(event)
            if len(at_ids) > 1:
                await event.send(event.plain_result("一次只能抽取一只小猪哦！"))
                return
            if len(at_ids) == 1:
                if at_ids[0] in self.admins_id:
                    await event.send(event.plain_result("你这只小猪，不许对主人不敬！"))
                    return
                user_id = at_ids[0]

        today_cache = self.load_json(self.today_path, {"date": "", "records": {}})
        if today_cache.get("date") != today_str:
            today_cache = {"date": today_str, "records": {}}
        records = today_cache["records"]

        rec = records.get(user_id, {"count": 0, "pigs": []})
        if rec["count"] >= self.daily_draw_count:
            await event.send(event.plain_result(f"今日抽取次数已用完（{self.daily_draw_count} 次）"))
            return

        drawn_ids = {str(p.get("id", "")) for p in rec.get("pigs", [])}
        pig = self.pick_pig(drawn_ids)
        if not pig:
            await event.send(event.plain_result("小猪信息加载失败，请检查后台报错！"))
            return

        rec["count"] = rec["count"] + 1
        rec["pigs"].append(pig)
        records[user_id] = rec
        self.save_json(self.today_path, today_cache)

        await self.send_rendered_pig(event, pig, user_id)

    async def send_rendered_pig(self, event: AstrMessageEvent, pig_data: dict, user_id: str):
        img_path = await asyncio.to_thread(self.render_pig_image, pig_data)
        if img_path and img_path.exists():
            try:
                chain = [Comp.Plain(". 这是你的每日小猪：")]
                group_id = event.get_group_id()
                if group_id:
                    chain.insert(0, Comp.At(qq=user_id))
                await event.send(event.chain_result(chain))
                await event.send(event.image_result(str(img_path.absolute())))
                logger.info("合成图片发送成功")
                return
            except Exception as e:
                logger.error(f"发送合成图片失败：{str(e)}")
            finally:
                try:
                    img_path.unlink(missing_ok=True)
                except Exception as cleanup_err:
                    logger.warning(f"清理临时图片失败：{cleanup_err}")

        await self.send_fallback_msg(event, pig_data)

    async def send_fallback_msg(self, event: AstrMessageEvent, pig_data: dict):
        pig_name = pig_data.get("name", "未知小猪")
        pig_desc = pig_data.get("description", "无描述")
        pig_analysis = pig_data.get("analysis", "无解析")

        text_msg = (
            f"【每日小猪】\n名称：{pig_name}\n描述：{pig_desc}\n解析：{pig_analysis}"
        )
        msg_chain = []

        img_path = self.resolve_image(pig_data)
        if img_path and img_path.exists():
            try:
                msg_chain.append(Comp.Image.fromFileSystem(str(img_path.absolute())))
            except Exception as e:
                logger.error(f"发送原始图片失败：{str(e)}")
                text_msg += "\n\n（图片发送失败，仅展示文字信息）"

        msg_chain.append(Comp.Plain(text_msg))
        await event.send(event.chain_result(msg_chain))

    async def terminate(self):
        logger.info("每日小猪插件已卸载")
