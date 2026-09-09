---
name: multi-media-processor
slug: deanyih-multi-media-processor
displayName: 多媒体处理助手
description: "多平台音视频下载、转写与内容处理（单一入口，无需中转其他技能）：视频号→解析原地址→下载→Whisper本地转写→核心观点；公众号→抓取正文→摘要；支持B站/YouTube/小红书/TikTok/微博/Dailymotion/Vimeo/抖音等平台的视频下载与转写；也支持直接传入本地音视频文件。输出格式支持 txt / srt / markdown / docx，共四种类型。纯文字类内容（公众号文章）不生成字幕，只输出 txt + markdown / docx 三种。"
version: 1.1.8
author: Dean Yih (adapted by 彤彤)
license: MIT
platforms: [windows, linux, macos]
trigger:
  - "帮我下载这个视频"
  - "转写这个视频"
  - "提取视频文字"
  - "下载公众号文章"
  - "生成字幕"
metadata:
  openclaw:
    requires:
      env:
        - LLM_API_KEY      # 可选，LLM 智能整理
        - LLM_BASE_URL     # 可选
        - LLM_MODEL        # 可选
      bins:
        - python3
        - ffmpeg           # 首次运行自动下载
---

# 多媒体处理（视频号 + 公众号 + 多平台视频）

用户发来 **微信视频号分享链接**（`weixin.qq.com/sph/...`）、**公众号文章链接**（`mp.weixin.qq.com/...`）或 **本地音视频文件路径**，自动处理并交付结果。

- **视频号**：`解析原视频地址 → 下载 → 本地 Whisper 转写 → 提炼核心观点`
- **公众号**：`抓取文章正文 → 结构化摘要/核心观点`
- **多平台视频**：`B站/YouTube/小红书/TikTok/微博/Dailymotion/Vimeo/抖音 → 下载 + 转写`
- **本地文件**：`直接传入本地视频/音频路径 → 复制到输出目录 → 转写`（支持 mp4/avi/mkv/mov/mp3/wav/flac 等常见格式）
- **输出格式**：`txt / srt / markdown / docx` 四选一或全部生成

> 所有大文件依赖（ffmpeg、Whisper CLI引擎+dll、Whisper模型）**首次运行相关任务时自动从国内镜像下载**，路径全部自动推导。
> **wx_channels_download 工具已原生支持全平台（Windows/macOS/Linux）**（v260817+）。

---

## 1. 快速调用

> 💡 **怎么用？** 把链接或文件拖进来，自动下载 + 转写 + 生成文档，**1-3分钟出结果（内容越长耗时也会越长）**。

### 1.1 真实案例

**场景 1：微信视频号**
你在朋友圈看到个视频号视频，点分享复制链接发给我："帮我提取这个视频的文字内容"，我就自动下载转写，产出 txt + srt + md + docx 四份文档。

**场景 2：多平台视频（B站/抖音/小红书/微博/TikTok/YouTube/Dailymotion/Vimeo）**
不管是哪个平台的视频链接，直接丢给我就行：
- **B 站/小红书/微博**：BV号完整链接或短链，自动解析出视频标题命名文件
- **抖音**：长链或短链，自动下载无水印视频并转写
- **YouTube/TikTok/Vimeo/Dailymotion**：复制分享链接，需要施展魔法（特殊网络环境），支持各种格式
我会自动处理，产出 txt + srt + md + docx 四份文档。如加 `--breakdown` 参数，额外生成结构化爆款拆解分析。

**场景 3：本地视频文件**
电脑里有录屏或下载的视频，告诉我路径，我复制到输出目录并转写。

**场景 4：微信公众号文章**
看到一篇公众号文章觉得有价值，把链接发给我："帮我提取这篇文章的核心要点"，我就抓取全文、生成结构化摘要，产出 txt + md + docx 三种文档（无字幕，因为文章本身是纯文字内容）。

### 1.2 支持的平台与限制

| 平台 | 状态 | 需要配置吗 |
|------|------|------------|
| 微信视频号 | ✅ | 需要元宝 Cookie（约数周一换） |
| 抖音 | ✅ | 需要 Cookie |
| 微信公众号 | ✅ | 不需要 |
| B 站/小红书/微博 | ✅ | 不需要 |
| YouTube/TikTok/Vimeo/Dailymotion | ✅ | 需要施展魔法 |
| 本地文件 | ✅ | 不需要 |

> 💡 **Cookie 说明**：
> - 视频号需要登录态（元宝 Cookie）
> - 抖音需要登录态（SessionID 等 Cookie）
> - B 站/本地文件**不需要任何配置**
> - YouTube/TikTok/Vimeo 需要特殊网络环境

### 1.3 Cookie 配置（双模式支持）

**模式1：自动获取（推荐）**
如果你在浏览器已成功登录以下平台：
- 元宝网页版（yuanbao.tencent.com）
- 抖音网页版（douyin.com）

技能会尝试从浏览器读取 Cookie 并自动配置，然后继续后续任务。

**模式2：手动粘贴**
如果自动获取失败，或你想手动配置：
1. 打开浏览器开发者工具（F12）
2. 访问对应网站并登录
3. Application → Cookies → 复制需要的 Cookie 值
4. 将 Cookie 字符串粘贴到会话窗口
5. 技能自动识别并配置

**抖音 Cookie 获取步骤**（详细版）：
1. 浏览器访问 https://www.douyin.com 并**成功登录**
2. 鼠标**点击进页面网站地址栏**，再按 **F12** → 选中 **Application（应用程序）**
3. 左侧展开 **Cookies** → 点击 **https://www.douyin.com**
4. **Ctrl+A** 全选「右侧显示出来的所有内容」→ **Ctrl+C** 复制 → 粘贴到会话窗口
5. 技能自动识别并配置

**元宝 Cookie 获取步骤**（详细版）：
1. 浏览器访问 https://yuanbao.tencent.com 并**成功登录**
2. 鼠标**点击进页面网站地址栏**，再按 **F12** → 选中 **Application（应用程序）**
3. 左侧展开 **Cookies** → 点击 **https://yuanbao.tencent.com**
4. **Ctrl+A** 全选「右侧显示出来的所有内容」→ **Ctrl+C** 复制 → 粘贴到会话窗口
5. 技能自动识别并配置

> 💡 **提示**：
> - 只需复制具体网址（如 `https://www.douyin.com`）下的 Cookie
> - 不需要复制其他 CDN 域名（如 `lf-zt.douyin.com`、`lf-rc1.yhgfb-cn-static.com`）
> - 如果粘贴后提示「已保存 N 个 Cookie」即配置成功

**Cookie 有效期**：
- 元宝 Cookie：约数周有效
- 抖音 Cookie：约1个月有效
- 过期后技能会提示重新获取

---

```bash
# 高精度转写（专业术语/嘈杂环境）
python "scripts/multi-media-processor.py" "<链接>" --model medium --denoise

# 只输出 Markdown
python "scripts/multi-media-processor.py" "<链接>" --format md

# 生成爆款拆解分析（可选）
python "scripts/multi-media-processor.py" "<链接>" --breakdown

# 只下载不转写
python "scripts/multi-media-processor.py" "<链接>" --no-whisper

# 仅在用户明确要求时：启用 LLM 智能整理（默认不使用）
python "scripts/multi-media-processor.py" "<链接>" --llm
```

**文本整理**：转写后自动整理——去除"呃""啊""那个""就是说"等口语化填充词、繁体转简体、**按语境补全标点**、分章节与段落。

> ⚠️ **默认全程不使用 LLM**，全部由本地规则引擎完成（离线可用、无 API 费用）。
> 只有在你**明确要求**时，才加 `--llm` 参数启用模型整理（需先配 `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`，或写入 `~/.config/multi-media-processor/llm.json`）。

**语境标点规则**（本地规则引擎，不依赖 LLM）：
- 句内：ASR 的空格分隔按语境转为**逗号**；"一 二 三"这类列举转为**顿号**
- 称呼语后自动加逗号：`妈妈 我中午…` → `妈妈，我中午…`
- 句末按语境判定：
  - **问号** — 结尾"吗/呢/么"、句首"怎么/为什么/谁"、A不A 结构（会不会/是不是）、"难道"
  - **感叹号** — 句首祈使（别/不要/快/赶紧/马上）、末分句祈使（"知道了，快去！"）、结尾"啊/呀/哇"
  - **句号** — 其余陈述句；并排除"我不知道/告诉我…"这类含疑问词实为陈述的包裹句式
**爆款拆解**：使用 `--breakdown` 参数生成结构化分析，包含核心观点、金句摘录、结构分析、关键词提炼四大部分。

**输出产物**（每个任务独立子目录；**所有文件统一用主题名前缀**）：

> 以主题「郭宇宁·乔董」为例，一个任务目录下全部产物：
> `<主题>.mp4`（视频）· `<主题>_raw.txt`（原始转写）· `<主题>_subtitle.srt`（字幕）· `<主题>_timestamped.txt`（带时间戳 TXT）· `<主题>.md`（Markdown 脚本）· `<主题>.docx`（Word 脚本）· `<主题>_拆解.md`（爆款拆解，需 `--breakdown`）

**脚本化排版**（md/docx 均按"章节 + 段落"组织，便于阅读）：

> - **md**：`# 标题` → `## 场景 N · 关键词` → 若干段落（段间空行）
> - **docx**：标题 + `Heading 2` 章节标题，正文**首行缩进 2 字符**、**1.5 倍行距**、**两端对齐**
> - 章节来源：配了 LLM 时由模型划分场景并起小标题；未配 LLM 时按语音停顿切分，标题为「场景 N · 该章实体词」
> - 中文字体按平台自适应（Windows 微软雅黑 / macOS 苹方 / Linux 文泉驿），并标记 `zh-CN`

> 公众号文章（纯文字类，无音频，不生成字幕）：
> `article.md` - 正文 Markdown · `transcript_raw.txt` - 纯文本备份 · `transcript.docx` - Word

**输出目录**：自动判断使用者当前工作空间，输出到 `<工作空间>/multi-media/<视频ID>/`。判定优先级：环境变量 `MULTI_MEDIA_OUTPUT` → 用户级配置 → 当前会话工作区 → 进程工作目录 → 最近活跃项目 → 用户主目录。无需手动配置路径，首次运行自动探测。

---

## 2. 安装

**首次安装后使用时需安装依赖环境，请在良好网络环境下进行，安装时间约5分钟。**

### 2.1 系统要求

| 平台 | 支持状态 | 说明 |
|------|----------|------|
| Windows | ✅ 完整支持 | 所有功能可用，包括视频号解析 |
| Linux | ✅ 完整支持 | 支持所有功能（需管理员权限启动工具） |
| macOS | ✅ 完整支持 | 支持所有功能 |

### 2.2 安装流程

本技能通过 **WorkBuddy 技能市场** 一键安装，无需手动操作。

安装完成后，首次运行任何功能时会自动完成以下事项：
- 自动安装 Python 包依赖（httpx / yt-dlp / requests）
- 自动下载大文件依赖：Whisper 模型 ~253MB、ffmpeg ~142MB、wx_channels_download 工具 ~8MB
- 自动布置 whisper-cli 运行库（dll 与 exe 同级）、预置关闭工具代理、清理解压残留目录
- 路径全部自动推导，**无需手动配置任何路径**

无需重启 WorkBuddy，安装后直接可用。

### 2.3 配置

首次运行任何功能时，技能会自动启动 `wx_channels_download` 工具（监听 `127.0.0.1:2022`）。

**视频号 Cookie 配置**（全平台需要）：
- 技能检测到 `bin/wx_channels_download/config.yaml` 中 `cloudflare.sphCookie` 为空时，会提示配置后再继续
- 打开该文件，把 `cloudflare.sphCookie` 填为**你自己的**元宝 Cookie 字符串
- 获取方式：浏览器登录 `yuanbao.tencent.com` → F12 DevTools → Application → Cookies → 复制全部值拼成 `k=v;k=v` 字符串
- Cookie 会定期过期：视频号解析报「no cookie」类错误时，重新抓取一份填入即可

> 安全说明：Cookie 仅保存在你本机 `config.yaml` 中，**仅本地向元宝/微信接口请求使用**。

---

## 3. 依赖说明

### 3.1 Whisper 模型选择

| 模型 | 大小 | 速度 | 准确率 | 适用场景 |
|------|------|------|--------|----------|
| tiny | ~150MB | 最快 | 低 | 快速预览 |
| base | ~1GB | 快 | 中等 | 草稿转写 |
| small | ~2GB | 中等 | 良好 | 通用场景（默认） |
| medium | ~5GB | 较慢 | 优秀 | 高质量/专业术语 |

> ⚠️ small 模型对中文医学术语/方言识别有限（如「悬雍垂」可能误识为「玄王锤书」），专业内容建议用 `--model medium --denoise`。

### 3.2 输出格式

默认 `all` 格式，自动生成：
- `transcript_raw.txt` - 纯文本（ASR 原始结果，未清洗）
- `subtitle.srt` - SRT 字幕（带正确时间戳）
- `transcript_timestamped.txt` - 带时间戳的 TXT（[HH:MM:SS.mmm --> ...] 格式）
- `transcript.md` - Markdown（标题 + 元信息 + **清洗后完整段落**）
- `transcript.docx` - Word 文档（带段落格式，**中文简体，完整段落**）
- `transcript_simplified.txt` - 简体版（如原文为繁体）
- `metadata.json` - 元数据（作者/描述/时间戳/文件大小）

> 💡 **说明**：Markdown 和 DOCX 输出经过文本清洗（去除口语填充词），并使用 zhconv 将繁体转为简体，输出完整段落便于阅读。原始转录保存在 `transcript_raw.txt` 供追溯。

格式选择：`--format txt` / `--format md` / `--format docx` / `--format all`

---

## 4. 故障速查

> 遇到问题？先看这里。按「现象」搜关键词，找到后按「处理」操作即可。

| 现象 | 是什么 / 原因 | 怎么办 |
|------|---------------|--------|
| 🔐 提示缺少 Python 包 | 首次运行没装 httpx/yt-dlp 等 | 按终端提示手动 `pip install httpx yt-dlp` |
| 🔌 端口 2022 起不来 | 上次运行的工具进程没退出 | 任务管理器结束 `wx_video_download.exe`，重跑 |
| ⚠️ 视频号报「此内容暂时无法播放」 | Cookie 失效或过期（约数周一换） | 打开 `bin/wx_channels_download/config.yaml`，刷新 `sphCookie` 值 |
| ⚠️ 视频号报「no cookie」 | `sphCookie` 字段为空 | 打开 config.yaml，把 `cloudflare.sphCookie:` 后的引号里填上你的元宝 Cookie |
| 🌍 非 Windows 平台视频不可用 | 工具需要正确启动（Linux/macOS 有时需 sudo） | 手动跑一次 `python scripts/install_dependencies.py` |
| 📦 依赖缺失提示 | 安装不完整 | 重跑 `python scripts/install_dependencies.py` 自动修复 |
| 🔧 whisper-cli 报缺 dll | DLL 文件没摆到对的位置 | 删掉 `bin/whisper/bin/whisper-cli.exe`，重跑任意任务自动修复 |
| 💾 磁盘占用大 | 临时下载缓存没清 | 运行 `python scripts/install_dependencies.py --cleanup` |
| 🐢 转写速度慢 | 默认 small 模型 ~2GB，中等速度 | 改用 `--model tiny` 或 `--model base` 加速 |
| 🎯 识别准确度低 | 专业术语/方言/嘈杂环境 | 换 `--model medium`（~5GB）+ 加 `--denoise` 降噪 |
| 📝 文本中有口语化填充词 | ASR 原始转录包含"呃""啊""那个"等 | 已自动清洗：Markdown/DOCX 输出时去除填充词，原始转录保留在 `transcript_raw.txt` |
| 💻 不断弹 CMD 黑窗 / 全系统断网 | 旧版工具默认开启系统代理，残留设置导致全网 ECONNREFUSED | 编辑 `bin/wx_channels_download/config.yaml`，把 `proxy:` 段改为 `enabled: false`；断网恢复：注册表 `HKCU\...\Internet Settings` 的 `ProxyEnable` 置 0 |
| 🔗 B 站短链 `b23.tv/xxxx` 报错 WinError 123 | 短链 URL 含非法字符，旧版直接当文件名用 | 已自动修复：新版本会解析出 BV 号再命名 |
| ⏱️ SRT 字幕时间戳全为 `00:00:00,000 --> 00:00:01,000` | 旧版只输出了纯文本，没拿到时间戳 | 已自动修复：新版 whisper-cli 直出带时间戳的 SRT |
| 📁 多个视频产物互相覆盖 | 旧版同平台视频放同一目录，后来的覆盖前面的 | 已自动修复：新版按 `平台/vid/` 独立子目录隔离 |
| 🈶 Word 里看是繁体，复制出来却是简体 | 不是文字错了——python-docx 默认模板把东亚字体绑成**日文字体**（ＭＳ 明朝／ＭＳ ゴシック），中文按日文字形渲染。底层 Unicode 仍是简体，所以粘贴出来是简体 | 已自动修复：新版显式绑定中文字体（Windows 微软雅黑／macOS 苹方／Linux 文泉驿）并标记 `zh-CN` |
| 🈚 DOCX/MD 里残留繁体字 | 运行环境缺 `zhconv`，回退到内置字典漏转 | 新版自动装 `zhconv`，并三级兜底（zhconv → opencc → 内置字典）。可手动：`pip install zhconv` |
| 🏷️ 输出文件名是一长串 ID/「抖音视频_xxx」 | 视频本身无标题，用了平台默认占位名 | 已自动修复：新版会从转写内容提炼主题命名（如「郭宇宁·乔董」）；配了 LLM 时由模型提炼更准 |

---

## 5. 参考

### 5.1 依赖来源

| 依赖 | 版本 | 上游仓库 | 下载源 |
|------|------|----------|--------|
| wx_channels_download | v260817 | [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) | GitHub Releases |
| whisper.cpp CLI | b4938 | [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | GitHub Releases + ghfast.top 镜像 |
| ffmpeg | 7.1 | [Gyan/codexffmpeg](https://www.gyan.dev/ffmpeg/builds/) | gyan.dev + ghfast.top 镜像 |
| Whisper 模型 | v1.5.0 (ggml-small-q8_0) | [ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp) | HuggingFace + ghfast.top 镜像 |
| yt-dlp | 2026.08.19 | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | PyPI (pip install) |

### 5.2 Python 依赖

| 包 | 用途 | 安装方式 |
|----|------|----------|
| httpx | 公众号文章抓取 | pip install httpx |
| yt-dlp | 多平台视频下载 | pip install yt-dlp |
| requests | 备用 HTTP 客户端 | pip install requests |
| zhconv | 繁体转简体 | pip install zhconv（已内置） |

### 5.3 可选依赖

如需更多功能可手动安装：
```bash
pip install openai-whisper    # 可选：使用 Python Whisper 替代
```
