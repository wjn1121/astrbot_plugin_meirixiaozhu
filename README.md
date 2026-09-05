# 🐷 每日小猪 — AstrBot QQ 群聊插件

> 群友发一句含「今日小猪」或「每日小猪」的消息，机器人随机抽一只专属小猪，合成图文卡片：图片 + 名称 + 描述 + 性格解析。

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Platform](https://img.shields.io/badge/platform-QQ_(aiocqhttp)-green)](#)
[![Version](https://img.shields.io/badge/version-1.0.0-orange)](metadata.yaml)
[![License](https://img.shields.io/badge/license-MIT-9cf)](LICENSE)
[![Author](https://img.shields.io/badge/author-wjn1121-lightgrey)](https://github.com/wjn1121)

---

## ⚡ 快速开始

1. **安装插件**：在 AstrBot WebUI 插件市场中搜索「每日小猪」一键安装，或手动放入 `data/plugins/astrbot_plugin_meirixiaozhu/`
2. **安装依赖**：WebUI → 插件管理 → 每日小猪 → 点击「安装依赖」按钮（`requests`、`Pillow`）
3. **直接使用**：在群里发一句 `今日小猪` 或 `每日小猪`，机器人随机抽取一只小猪并发送图文卡片

不需要 `/` 指令、不需要 @机器人、不需要任何 API Key。内置素材 **开箱即用**。

---

## ✨ 功能特性

- 🐷 **双关键词触发**：消息中包含「今日小猪」或「每日小猪」即可，自然语言交互
- 🎲 **每日抽卡**：每人每天默认可抽 1 次，可在配置中修改次数，每天 0 点自动重置
- 🖼️ **图文卡片**：PIL 合成 800×800 卡片，含小猪图片、名称、描述、性格解析
- 📦 **本地 + PigHub 双池**：本地继承 rollpig 的基础小猪，另加入 PigHub 热度前 100 只新猪
- 🧠 **固定性格解析**：每只小猪配一段同风格文案，已按图片逐一编写并固定
- 🗜️ **图片压缩缓存**：PigHub 图片本地压缩为 WebP / GIF，清晰且体积小
- 👥 **艾特他人查看**（可选）：开启后可 `每日小猪 @某人` 查看对方的小猪
- 🛡️ **降级容错**：图片缺失或合成失败时自动降级为纯文本回复
- ⚙️ **可视化配置**：次数、艾特开关均在 WebUI 面板调节

---

## 🎬 效果演示

```
群友: 每日小猪
Bot:  . 这是你的每日小猪：
      [图片卡片：小猪图片 + 名称 + 描述 + 性格解析]
```

```
群友: 今天抽一发今日小猪
Bot:  . 这是你的每日小猪：
      [图片卡片：另一只小猪]
```

---

## 🎯 触发规则

| 你说的话 | 是否触发 |
|----------|:------:|
| `今日小猪` | ✅ |
| `每日小猪` | ✅ |
| `抽一发每日小猪` | ✅ |
| `今天来个今日小猪` | ✅ |
| `小猪` | ❌ |
| `猪` | ❌ |
| `今日` | ❌ |

> 💡 仅匹配包含「**今日小猪**」或「**每日小猪**」的消息，单独的「小猪」「猪」等不会误触发。

**生效范围**：仅 **QQ 群聊**（aiocqhttp 适配器），私聊及其他平台不触发。

---

## 📥 安装方式

### 方式一：插件市场（推荐）

在 AstrBot WebUI → 插件市场 → 搜索「**每日小猪**」→ 一键安装

### 方式二：手动安装

```bash
# 克隆到 AstrBot 插件目录
cd AstrBot/data/plugins/
git clone https://github.com/wjn1121/astrbot_plugin_meirixiaozhu.git

# 安装依赖
pip install -r astrbot_plugin_meirixiaozhu/requirements.txt
```

安装后重载插件即可生效。

---

## ⚙️ 配置项

在 AstrBot WebUI → 插件配置面板中可调：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `daily_draw_count` | 整数 | `1` | **每人每日可抽取次数**（数字输入框） |
| `at_view_pig` | 开关 | `false` | 开启后可用 `每日小猪 @某人` 查看对方的小猪 |

---

## 🐖 小猪池与素材

小猪池由两部分组成：

| 部分 | 说明 |
|------|------|
| **本地基础** | `resource/pig.json` + `resource/image/`（继承 rollpig，含固定文案，96 张图） |
| **PigHub 扩充** | `resource/pig_extended.json` + `resource/pighub/`（PigHub 热度前 100，本地压缩缓存） |

每只小猪包含：`id`、`name`、`description`（一句诙谐描述）、`analysis`（固定性格解析）、`image`（图片路径）。

如需**重新拉取 / 更新 PigHub 小猪**（按热度取前 100 并压缩缓存），在插件目录执行一次：

```bash
python sync_pighub.py
```

> `sync_pighub.py` 会保留已编写好的性格文案，仅刷新/补充图片与文案缺失的小猪。

---

## 📁 目录结构

```
astrbot_plugin_meirixiaozhu/
├── main.py               # 插件入口，核心逻辑（抽卡、卡片渲染、每日缓存）
├── metadata.yaml         # 插件元信息（市场显示用）
├── _conf_schema.json     # WebUI 可视化配置定义
├── requirements.txt      # Python 依赖（requests、Pillow）
├── sync_pighub.py        # 一次性脚本：拉取 PigHub 热度前100、压缩缓存、生成合并池
├── README.md             # 本文件
└── resource/
    ├── pig.json          # 本地基础小猪数据（含固定文案）
    ├── pig_extended.json # 合并后的完整小猪池（本地 + PigHub，含固定文案）
    ├── image/            # 本地基础小猪图片（96 张）
    ├── pighub/           # PigHub 热度前100 图片的本地压缩缓存（WebP / GIF）
    └── font/             # 卡片渲染字体（可选，缺省回退系统字体）
```

---

## 🔧 技术特点

- **被动正则匹配**：`@filter.regex(r"今日小猪|每日小猪")`，无需唤醒词，不消耗 LLM token
- **事件过滤**：叠加装饰器限定 QQ 群聊（AIOCQHTTP + GROUP_MESSAGE）
- **PIL 卡片渲染**：800×800 白底卡片，图片、名称、描述、解析双居中，解析自动换行
- **跨平台字体**：优先插件内字体，其次系统字体，最后 PIL 默认字体
- **每日缓存**：按用户记录当天已抽次数与结果，0 点自动重置
- **CPU 密集转线程**：图片合成用 `asyncio.to_thread()`，不阻塞事件循环
- **临时文件清理**：卡片图片发送后自动删除，不留磁盘垃圾
- **错误隔离**：图片缺失、下载失败、合成异常均降级为纯文本，不崩溃

---

## 📋 兼容性

| 项目 | 要求 |
|------|------|
| AstrBot | ≥ 4.16 |
| 平台 | QQ（aiocqhttp 适配器） |
| Python | ≥ 3.10（随 AstrBot 环境） |

---

## 🆕 更新日志

### v1.0.0

- 🎉 初始发布
- 🐷 每日小猪抽卡，关键词「今日小猪 / 每日小猪」触发
- 📦 本地素材（继承 rollpig）+ PigHub 热度前 100 新猪
- 🧠 每只小猪固定性格解析，文案匹配图片风格
- 🗜️ PigHub 图片本地压缩缓存（WebP / GIF）
- ⚙️ WebUI 支持「每人每日抽取次数」与「艾特他人查看」开关

---

## 🙋 FAQ

**Q: 为什么不用 `/每日小猪` 指令？**

A: 群聊是自然语言场景，直接说「每日小猪」比 `/每日小猪` 更符合聊天直觉，降低使用门槛。

**Q: 可以改成别的触发词吗？**

A: 编辑 `main.py` 中 `@filter.regex(r"今日小猪|每日小猪")` 的正则表达式，改为你想要的触发词后重载插件即可。

**Q: 每人每天能抽几次？**

A: 默认 1 次。在 WebUI 配置面板把「每人每日可抽取次数」改成任意数字即可；达到次数后当天不再抽取，0 点自动重置。

**Q: 怎么查看别人的小猪？**

A: 开启配置项 `at_view_pig` 后，发送 `每日小猪 @某人` 即可查看对方的小猪（仅限非管理员用户）。

**Q: 小猪卡片里图片不显示？**

A: 检查 `resource/image/` 或 `resource/pighub/` 下是否有对应图片。图片缺失时插件会自动降级为纯文本回复。

**Q: 能和 rollpig（今日小猪）一起用吗？**

A: 能，但因为两者都响应「今日小猪」，同一条消息可能被两个插件各处理一次。如需避免，可调整其中一个的触发词（如只保留 `每日小猪`）或只启用其一。

---

## 📞 联系作者

- **QQ 群**：1038882918
- **GitHub**：[@wjn1121](https://github.com/wjn1121)

---

<p align="center">如果这个插件给你带来了一只独一无二的小猪，请点个 ⭐ Star 支持一下～</p>
