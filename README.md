<div align="center">

# astrbot_plugin_meirixiaozhu
_✨ [astrbot](https://github.com/AstrBotDevs/AstrBot) 每日小猪 ✨_

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.16%2B-orange.svg)](https://github.com/Soulter/AstrBot)

</div>

## 🌟 项目介绍

每天抽一只专属“每日小猪”，生成配图展示名称、描述和性格解析。

- 小猪池 = **本地素材**（继承自 rollpig）+ **PigHub 热度前 100** 新猪。
- 每人每日默认可抽 **1 次**，可在配置中修改。
- 性格解析为**固定文案**，延续 rollpig 的诙谐风格，并匹配每张小猪图片。
- 小猪图片已本地压缩缓存，清晰且体积小。

## 📦 安装

```bash
# 克隆仓库到插件目录
cd /AstrBot/data/plugins
git clone https://github.com/wjn1121/astrbot_plugin_meirixiaozhu

# 控制台重启 AstrBot
```

## 🐷 使用 🐷

**每日小猪** —— 抽取今天属于你的小猪 🐖

- 每个用户每天默认只能抽取一次，可在配置中修改次数。
- 重复抽取会返回当天已抽到的结果，每天 0 点自动重置。
- 开启「艾特他人查看」后，可 `每日小猪 @某人` 查看对方的小猪。

## ⚙️ 配置

在 AstrBot 插件配置（WebUI）中：

- `at_view_pig`：是否允许艾特他人查看对方小猪（默认关）。
- `daily_draw_count`：每人每日可抽取次数（默认 1，数字输入框）。

## 🐖 小猪池与素材

- `resource/pig.json` + `resource/image/`：本地基础素材（继承 rollpig）。
- `resource/pig_extended.json`：合并后的完整小猪池（含每只小猪的固定文案与图片路径）。
- `resource/pighub/`：PigHub 热度前 100 图片的本地压缩缓存（WebP / GIF）。

如需**重新拉取 / 更新 PigHub 小猪**（按热度取前 100 并压缩缓存），在插件目录执行一次：

```bash
python sync_pighub.py
```

> `sync_pighub.py` 会保留已编写好的性格文案，仅刷新/补充图片与文案缺失的小猪。

## 🎖️ 致谢

- 本地小猪素材与写作风格参考 [astrbot_plugin_rollpig](https://github.com/MegSopern/astrbot_plugin_rollpig)。
- 新猪图片来自 [PigHub](https://www.pighub.top)。

## 📜 许可证

本项目采用 [MIT 许可证](LICENSE) 开源。
