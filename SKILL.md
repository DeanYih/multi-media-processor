---
name: multi-media-processor
description: "多平台音视频下载、转写与内容处理（单一入口，无需中转其他技能）：视频号→解析原地址→下载→Whisper本地转写→核心观点；公众号→抓取正文→摘要；支持B站/YouTube/小红书/TikTok/微博/Dailymotion/Vimeo/抖音等平台的视频下载与转写；也支持直接传入本地音视频文件。输出格式支持 txt / srt / markdown / docx，共四种类型。纯文字类内容（公众号文章）不生成字幕，只输出 txt + markdown + docx 三种。"
version: 1.3.8
author: Dean Yih
platforms: [windows, linux, macos]
agent_created: true
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

**脚本化排版**（md/docx 均按"两级章节 + 段落"组织，便于阅读）：

> - **md**：`# 标题` → `## [mm:ss] 一级章节·实体+动作概括` → `### [mm:ss] 二级小节` → 若干段落（段间空行）
> - **docx**：标题 + `Heading 2` 一级章节 / `Heading 3` 二级小节，正文**首行缩进 2 字符**、**1.5 倍行距**、**两端对齐**
> - 章节来源：配了 LLM 时由模型划分场景并起小标题；未配 LLM 时走**语义章节划分**（v1.3.5+）
> - 语义章节划分（TextTiling 式，纯本地规则）：相邻窗口词汇相似度（Jaccard）+ 时间停顿加成 → 边界强度 → 自适应 top-N 强边界切**少量一级核心章节**（约每40句一章，方便快速扫重点）→ 仅超长章节（>45 句）才分二级小节（至多3个）→ 尾段碎片并回（v1.3.5+ 核心章节版）
> - 概括标题生成：领域实体（从术语词典 keywords 动态加载）+ 动词名词化组合，如「显存更换与报错测试·失败」；含时间戳前缀 `[mm:ss]`；结果标记词（白瞎/搞定）→ 后缀
> - SRT 回退：`_load_srt_segments` 优先 subtitle.srt，回退 `*_raw.srt`（sph 视频号流程产物）；术语修正同步写回 SRT（章节正文来自 SRT）
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

**视频号 Cookie 配置**（全平台需要，密钥走环境变量，不落盘进技能目录）：
- 元宝 Cookie 保存在统一密钥文件 `~/.workbuddy/.secrets.env`，变量名为 `WX_SPH_COOKIE`
- 技能启动时自动从该变量注入到运行时 `config.yaml` 的 `cloudflare.sphCookie`（exe 只认 config.yaml，不读环境变量）
- 写入方式（**不要 setx**：元宝 Cookie 超长会触发 Windows 1024 字符上限被截断，`.secrets.env` 无此限制）：
  - 推荐：在 `~/.workbuddy/.secrets.env` 追加一行 `WX_SPH_COOKIE=<k=v;k=v 字符串>`
  - 或用统一模块：`python ~/.workbuddy/lib/secret_store.py` 查看；`put_secret("WX_SPH_COOKIE", "...", persist_env=False)` 写入
- 获取方式：浏览器登录 `yuanbao.tencent.com` → F12 DevTools → Application → Cookies → 复制全部值拼成 `k=v;k=v` 字符串
- Cookie 会定期过期：视频号解析报「no cookie / Cookie 缺失」类错误时，重新抓取一份覆盖该变量即可

> **解析走两个组件（已实测可用）**：① **本地 API 服务**——工具启动后监听 `127.0.0.1:2022`，提供 `/api/channels/parse_sph`、`/api/status` 等接口（对应官网 API Playground 页面）；② **元宝 Cookie**（`WX_SPH_COOKIE` → 注入到 `cloudflare.sphCookie`）——API 服务用它向元宝后端换出视频直链。两者配合：缺 Cookie 时仅组件②报错（重抓即可），API 服务本身始终可启。注意 `cookie:` 段（CookieCloud 跨设备同步）与本解析无关，留空不影响视频号下载。

> 安全说明：元宝 Cookie **主副本在 `~/.workbuddy/.secrets.env`（本机用户可读，权限 600，不进任何仓库）**，技能目录的 `config.yaml` 仅运行时由脚本注入、且发布包不含 `bin/`，不会随技能泄露。下载完成后脚本会自动把 `config.yaml` 里的 `sphCookie` 清空，避免明文长期落盘。

**视频号解析（单一通道：本地 exe + 本地元宝 Cookie）**

技能只保留一条**已实测可用**的视频号解析通道：本机启动 `wx_video_download.exe`（监听 `127.0.0.1:2022`），调用其 `/api/channels/parse_sph` 接口，用**本机**的元宝 Cookie（`WX_SPH_COOKIE` → 注入运行时 `config.yaml` 的 `cloudflare.sphCookie`）换出视频直链，再下载 + 本地 Whisper 转写。

- 解析流程里的「本地 API 服务」与「元宝 Cookie」是两个**组件**而非两种模式：① 本地 API 服务（工具自带，始终可启）；② 元宝 Cookie（缺它时仅解析报错，重抓即可）。
- 调用方**需要**本机持有元宝 Cookie（约数周一换）——这是当前唯一端到端跑通的方式。
- 安全收尾：下载完成后脚本自动清空 `config.yaml` 里的 `sphCookie` 明文，避免长期落盘。

> 注：历史上评估过两种「调用方免 Cookie」方案——自部署 sph Worker（服务端持 Cookie）与 Bridge（需常开设备）——因额外部署成本 / 未端到端验证而未启用，相关代码已归档至 `scripts/_deprecated/`，日后若需要可恢复。

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
| ⚠️ 视频号报「此内容暂时无法播放」 | Cookie 失效或过期（约数周一换） | 重新抓取元宝 Cookie，覆盖 `~/.workbuddy/.secrets.env` 的 `WX_SPH_COOKIE` 行 |
| ⚠️ 视频号报「no cookie」 | `WX_SPH_COOKIE` 未设置或为空 | 在 `~/.workbuddy/.secrets.env` 写入 `WX_SPH_COOKIE=<k=v;k=v 字符串>`（不要用 setx，超长会被截断） |
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
| 🔤 歌词/字幕没识别出来 | ASR 听不到画面硬编码文字（唱歌视频常见问题） | 加 `--ocr` 参数启用视频帧 OCR；字幕类建议再加 `--ocr-bottom 0.35` |
| 🤖 OCR 模块不可用 | 未安装 paddleocr / paddlepaddle | `pip install paddleocr paddlepaddle`（CPU 版），首次运行自动下载模型 |
| 💥 OCR 报 `ConvertPirAttribute2RuntimeAttribute not support` | paddle≥3.3 的 oneDNN Bug | 已内置 `enable_mkldnn=False` 规避；若自建调用，初始化时务必带上该参数 |
| 📏 OCR 报「无法读取视频时长」 | 时长读取误用了 ffmpeg（`-show_entries` 是 ffprobe 语法） | 已修复为 `video_duration()`：ffprobe 优先，失败解析 ffmpeg stderr |
| 🔠 英文转写被塞进中文标点 | 中文标点规则误伤英文（空格→`，`） | 已修复：新增 `_is_cjk_text()` 门控，非中文文本跳过全部中文标点规则 |
| 🏷️ 输出文件名是一长串 ID/「抖音视频_xxx」 | 视频本身无标题，用了平台默认占位名 | 已自动修复：新版会从转写内容提炼主题命名（如「郭宇宁·乔董」）；配了 LLM 时由模型提炼更准 |
| 💥 视频号 exe 起不来、端口 2022 拒绝连接（`WinError 10061`），`stdout.log` 末尾报 `yaml: line N: did not find expected key` | 注入元宝 Cookie 时把 `sphCookie` 的 2 空格缩进抹掉——它是 `cloudflare` 块的子项，被提到顶层后，紧随其后仍缩进的 `sphCredential` 成了 YAML 孤儿，exe 解析配置失败即退出 | 已自动修复（v1.3.7）：`_write_sph_cookie` / `_clear_sph_cookie_in_config` 改为逐行处理、保留原行缩进。应急：手动给 `bin/wx_channels_download/config.yaml` 的 `sphCookie` 行补回 2 空格缩进 |
| 🎬 视频号解析成功却没有 `video.mp4`，Whisper 报 `转写失败 (exit=1)`（日志从「1. parse_sph」直接跳「3. Whisper」，缺「2. 下载」） | v1.3.6 简化时 `_sph_download_transcribe` 误删了下载步骤，拿到 `video_url` 后直接送 Whisper，文件不存在自然失败 | 已自动修复（v1.3.7）：补回 httpx 流式下载（腾讯 CDN 直链带 UA 即可 200，无需特殊 header/referer） |
| 🌐 转写里出现**大段重复同一句**（某句连读几十次），或英文视频被转成中文、中文视频被转成英文 | **语言参数给错**。`-l zh` 落在英文原声上会"翻译式"解码，低置信段退化为自回归复读；`-l en` 落在中文解说上则会译成英文。另：whisper-cli 的 `-l auto` **只按开头 30 秒判语种**，中英混音视频必然判错 | 用 `--lang auto`（v1.3.8 起改为**分块语种检测**：每 30 秒重新判一次语种，日志打出「语言地图」）；已知纯语种时直接 `--lang zh`/`--lang en` 更快 |
| 🔍 想确认音频到底是什么语种 | — | `python bin/whisper/scripts/transcribe.py <音频> --lang auto`，看日志里 `auto-detected language: xx (p=…)`；或按 MV 画面硬字幕/口型交叉验证 |

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

按需安装，不装不影响主流程（对应能力自动降级）：
```bash
# 场景识别增强（--enhance）：镜头检测 + 人声分离 + 音频事件标签
pip install "scenedetect[opencv]" demucs panns_inference librosa

# 画面文字 OCR（--ocr）
pip install paddleocr paddlepaddle
```

> **注意**：`paddlepaddle` 要求 `numpy<2.4`，若环境已装更新版 numpy（如 torch 带的 2.5.x），
> 安装时 pip 会自动降级；降级后用 `python -c "import paddle"` 验证是否可导入。
> numpy 2.3.x 与 torch 2.5.x + paddle 3.3.x 可共存。

### 5.4 场景识别增强（v1.1.8+）

启用 `--enhance` 参数后，技能会自动：
1. **镜头检测** - 使用 PySceneDetect 识别视频转场点，替代固定时间间隔切分章节
2. **人声分离** - 使用 demucs 分离人声与背景音乐，提升唱歌/带BGM视频的转写质量
3. **音频分类** - 使用 PANNs (CNN14) 识别音频事件（音乐、唱歌、掌声、笑声等），在输出中自动标注

**适用分支**：视频号、公众号、多平台视频、本地文件**均支持**（v1.2.0 起视频号分支已补齐，此前会静默失效）。

**输出位置**：音频事件写入 Markdown 末尾的「音频事件标记」章节，形如：
```
- `00:00` 【说话】、【音乐】
- `00:10` 【说话】
```

**行为说明**：
- 人声分离**按需触发**：先用 PANNs 判断是否存在音乐/唱歌，无音乐则跳过（避免白白跑 demucs）。日志会显示「未检测到音乐/唱歌，跳过人声分离」。
- 镜头检测为 **0 转场属正常**（一镜到底/静态讲解类视频），此时自动回退到按时间间隔切章。

**前置条件**：
- 权重文件：`~/panns_data/Cnn14_mAP=0.431.pth` (~312MB)
- 类标签：`~/panns_data/class_labels_indices.csv`
- demucs 模型：首次运行时自动下载 (~1.5GB)

**示例用法**：
```bash
# 完整增强流程（镜头检测 + 人声分离 + 音频标签）
python "scripts/multi-media-processor.py" "<链接>" --enhance --model medium --format md

# 仅音频分类（轻量）
python "scripts/multi-media-processor.py" "<链接>" --enhance --no-whisper
```

### 5.5 视频帧 OCR（v1.1.9+，v1.2.0 修复，v1.2.2 加画面门控）

启用 `--ocr` 参数后，技能会抽帧运行 PaddleOCR 识别画面中的文字（歌词、字幕等硬编码文字）。

**功能说明**：
- 按间隔抽取视频帧，PaddleOCR(PP-OCRv6) 识别文字
- 结果以 `- `时间戳` 文本` 形式追加到 Markdown 末尾的「画面文字（OCR）」章节
- 自动繁转简，重复帧去重（回看 3 条，抑制 A/B 抖动）
- 抽帧文件写入系统临时目录，**不污染输出目录**

**纯音频自动跳过（v1.2.2+）**：
输入是纯音频文件（mp3 / wav / flac / m4a 等，无视频流）时，`--ocr` 会自动跳过并打印
`[OCR] 输入为纯音频（无视频流），跳过画面文字识别`——没有画面可识别，强行跑只会白等并返
回 0 条。判定用 `ffprobe` 查视频流，ffprobe 不可用时回退按扩展名判断。
镜头检测（转场切章）同样受此门控，纯音频不会去跑 scenedetect。
人声分离与音频事件**不受影响**（纯音频照样能做）。

**参数**：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--ocr` | 关 | 启用画面文字识别 |
| `--ocr-interval` | 2.0 | 抽帧间隔（秒）。越小越准但越慢 |
| `--ocr-bottom` | 全画面 | 只识别画面底部该比例区域（如 `0.35`）。**字幕/歌词强烈推荐**，可减少画面其它文字干扰并提速 |
| `--ocr-max-frames` | 150 | 最大抽帧数，超出自动放大间隔，防止长视频跑飞 |
| `--ocr-diff` | 2.5 | 帧间差异阈值，**最主要的提速开关**。低于此值视为画面未变而跳过识别，实测可省 60%~85% 的帧；设 `0` 关闭预筛（逐帧识别，最慢最全） |
| `--ocr-max-side` | 960 | 抽帧时限制长边像素。竖屏 1080×1920 缩到 960 后检测面积减少约 4 倍；设 `0` 关闭缩放 |

**前置条件**：
- `paddleocr>=2.7.0.1` 和 `paddlepaddle>=2.5.0`（CPU 版）
- 首次使用会下载 OCR 模型（约 200MB，PP-OCRv6 det+rec），之后缓存复用

**示例用法**：
```bash
# 仅 OCR（不加 --enhance 也支持）
python "scripts/multi-media-processor.py" "<链接>" --ocr --format md

# 字幕/歌词类视频：只扫底部 35% 区域，1 秒一帧
python "scripts/multi-media-processor.py" "<链接>" --ocr --ocr-bottom 0.35 --ocr-interval 1.0 --format md

# OCR + 增强（镜头检测+人声分离+OCR 三重）
python "scripts/multi-media-processor.py" "<链接>" --enhance --ocr --model small --format md
```

**已知环境坑（代码已内置规避，勿删）**：
- **paddle≥3.3 + oneDNN 崩溃**：报 `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`。
  解法是初始化时传 `enable_mkldnn=False`（实测 `FLAGS_enable_pir_api=0` 无效）。代码已在 `scene_audio._OCR_KWARGS_V3` 内置，并有 2.x 参数回退。
- **PaddleOCR 3.x 无 `use_angle_cls`**：3.x 该参数已废弃，改名为 `use_textline_orientation`。
- **时长读取必须用 ffprobe**：`-show_entries format=duration` 是 ffprobe 语法，ffmpeg 不支持。
  代码用 `video_duration()` 走 ffprobe，失败再解析 ffmpeg stderr 的 `Duration:`。

**注意**：OCR 对模糊、倾斜、艺术字体的识别效果有限，主要对标准宋体/黑体字幕效果最佳。
若返回 0 条，先确认画面是否真有硬编码文字（部分视频的字幕是播放器叠加层，不在画面像素里，无法 OCR）。

### 5.7 性能优化（v1.2.0+）

v1.2.0 对两个主要瓶颈做了实测优化（基准：16 核 CPU，2 分 17 秒英文视频）。

**① Whisper 转写：显式指定线程数**
`whisper-cli` 的 `-t` 默认仅 **4** 线程，在 16 核机器上严重浪费。实测：

| 线程数 | 耗时 |
|--------|------|
| 默认 4 | 50.8s |
| **8** | **38.6s**（最优，快 24%） |
| 16 | 43.2s（超线程竞争，反而变慢） |

代码已默认取 `min(8, CPU核心数)`。可用 `--threads N` 覆盖（主命令与 transcribe.py 均支持）。

**② OCR：三处优化，实测提速一个数量级**

| 优化项 | 前后对比 | 说明 |
|--------|----------|------|
| 一次性抽帧 | 38s → 3.7s | 原来逐帧调用 ffmpeg（68 次进程启动 + seek），改为 `fps` 滤镜一次抽完 |
| 帧差异预筛 | 68 帧 → 约 11~28 帧 | 画面没变则字幕没变，直接跳过 OCR，省 60%~85% |
| 限制长边 960 | 检测面积 ↓约 4 倍 | 竖屏 1080×1920 全尺寸送检是主要耗时源 |

**③ 想要更快的调参建议**
```bash
# 快速预览（先跑通看效果）：小模型 + 粗间隔
python "scripts/multi-media-processor.py" "<链接>" --model tiny --ocr --ocr-interval 3 --format md

# 只要转写不要 OCR（OCR 是可选步骤，关掉最快）
python "scripts/multi-media-processor.py" "<链接>" --model small --format md

# 字幕视频推荐配置（只扫底部 + 预筛）
python "scripts/multi-media-processor.py" "<链接>" --ocr --ocr-bottom 0.35 --ocr-interval 1.5 --format md
```

**准确率相关**：
- 模型越大越准：`tiny` < `base` < `small`（默认）< `medium`，代价是耗时。
- `--lang auto`：**分块语种检测**（v1.3.8+，每 30 秒重判一次语种，中英混音视频必用；比单次整段慢数倍）。
- 已知语种时显式指定（`--lang zh` / `--lang en`）更快也更稳；**不要**拿 `zh` 去转英文原声（会出复读幻觉）。
- 唱歌/带 BGM 视频用 `--enhance`：会自动判断有无音乐，有则先做人声分离再转写。
- 专有名词多的内容可传 `--prompt "术语1 术语2"`（主命令与 transcribe.py 均支持）提升识别。

### 5.8 唱歌/带 BGM 视频：OCR 比人声分离更有效（v1.2.1 实测）

**结论先说**：对唱歌视频，`--ocr` 识别画面硬编码歌词的质量**显著高于** Whisper 转写，
而 `--enhance` 的人声分离（demucs）对准确率**提升有限**。两者建议同时开，但读结果时以 OCR 为准。

实测样本：1 分 40 秒翻唱视频（人声 + BGM），Whisper `medium` 与人声分离组合：

| 片段 | Whisper medium（人声分离后） | OCR 画面歌词（正确） |
|------|------------------------------|----------------------|
| 00:30 | 说吵吵吵吵就到 | **说曹操曹操就到** |
| 00:38 | 首歌上还能如此被讨 | **受个伤还能如此配套** |
| 00:52 | 小心而喷喷到 | **小心儿，砰砰跳** |
| 01:12 | 没大学不用跳 | **没得选，不用挑** |
| 01:16 | 只配得五个主要 | **适配得无可救药** |

**原因**：歌声的音高、拖腔、气息与语音模型训练分布差异大，ASR 天然吃亏；
而画面歌词是作者直接打上去的确定文本，只要文字与背景对比度够，OCR 几乎不会错。

**推荐用法**：
```bash
# 唱歌/歌词视频：OCR 为主，转写为辅
python "scripts/multi-media-processor.py" "<链接>" --model medium --enhance --ocr --ocr-interval 1.5
```
- 结果 Markdown 末尾的「画面文字（OCR）」段落带时间戳，可当作歌词时间轴用。
- 若画面有固定标语（如"禁止吸烟"牌）反复被识别，用 `--ocr-bottom 0.35` 只扫底部字幕区。
- **v1.2.3+ 支持 OCR 校正 ASR**：用 `--ocr-correct` 开关，按时间窗口对齐画面 OCR 与 Whisper SRT，
  相似度达标则用 OCR 歌词替换 ASR 转写，生成 `corrected_subtitle.srt` 和校正后的 txt/md/docx。

### 5.6 原语言输出（v1.1.9+）

默认 `--lang zh` 会把音频强制按中文转写。**若视频原声是英文，应显式指定语言**，才能得到英文原文而非中文音译。

| 参数值 | 适用场景 |
|--------|----------|
| `--lang zh` | 中文视频（默认），最快 |
| `--lang en` | 英文视频，输出英文原文 |
| `--lang auto` | **分块语种检测**（v1.3.8+）：每 30 秒重新判一次语种，适合**中英混音**视频；比单次整段慢数倍 |

**⚠️ 语言给错会出幻觉（2026-09-20 实测）**：把 `zh` 用在英文原声上，Whisper 会"翻译式"解码，
并在低置信段退化为**自回归复读**（实测某视频 58–120s 把「伯克利读的评论」复读了 48 次）；
把 `en` 用在中文解说上，则会把整段中文译成英文。**中英混音视频一律用 `--lang auto`**——
v1.3.8 起 `auto` 不再是「只看开头 30 秒」，而是分块检测，并在日志打印语言地图：

```
[auto] 分块语种检测：时长 324.1s，切片 30s / 步长 29s，共 12 片
[auto] 语言地图：
  00:00:00 ~ 00:00:30  ->  zh (p=0.9999)
  00:00:58 ~ 00:01:28  ->  en (p=0.9948)   ← 英文采访插段，正确识别
  00:01:27 ~ 00:01:57  ->  zh (p=0.9993)
```

**行为说明**：
- 非中文文本自动**跳过中文标点化**，保留英文标点与空格（不会把空格误转成中文逗号）
- 非中文文本**不生成** `transcript_simplified.txt`（繁转简对英文无意义）
- 英文句子间自动补空格，不会出现 `day.Many` 粘连

**示例用法**：
```bash
# 英文视频输出英文原文
python "scripts/multi-media-processor.py" "<链接>" --lang en --format md

# 分块语种检测（中英混音视频推荐）
python "scripts/multi-media-processor.py" "<链接>" --lang auto --format md

# 英文视频 + OCR
python "scripts/multi-media-processor.py" "<链接>" --lang auto --ocr --format md
```

### 5.7 双语转写（v1.3.1+）

启用 `--bilingual` 参数后，技能会检测转写文本中的英文部分，并在其后附加中文翻译。

**行为说明**：
- 仅当文本主要含拉丁字符（英文）时触发翻译
- 纯中文文本保持原样，不添加翻译
- 翻译源优先级：LLM API > deep_translator（Google Translate）> 返回原文

**使用方式**：
```bash
# 启用双语转写（命令行参数）
python "scripts/multi-media-processor.py" "<链接>" --bilingual --format all

# 或通过环境变量
export BILINGUAL_ENABLED=1
python "scripts/multi-media-processor.py" "<链接>" --format all
```

**输出示例**：
```markdown
## 场景 1

Get good at repair.
**中文翻译：** 把修复练好。

So what is repair?
**中文翻译：** 那么什么是修复？
```

**注意**：
- 翻译功能需要网络连接（使用 Google Translate API 或 LLM API）
- 若网络受限或代理失败，自动降级为原文
- 建议在无代理环境或使用 LLM API 时启用以获得最佳效果
- LLM API 配置：设置环境变量 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 并配合 `--llm --bilingual`

### 5.9 OCR 校正 ASR（v1.2.3+）

对唱歌视频，Whisper 转写差（如"说草草草草"）而画面 OCR 歌词准（"说曹操曹操就到"）。
开启 `--ocr-correct` 后，技能会：

1. 解析 SRT 字幕的时间段
2. 将同时间段附近的 OCR 歌词片段按相似度对齐
3. 相似度 ≥ 阈值时，用 OCR 替换 ASR 文本
4. 输出 `corrected_subtitle.srt`（校正后可直接导入剪辑软件）
5. 同时更新 `_raw.txt`、`_timestamped.txt`、Markdown、DOCX 中的正文

**用法**：
```bash
# 唱歌/歌词视频推荐
python "scripts/multi-media-processor.py" "<链接>" --enhance --ocr --ocr-correct --ocr-bottom 0.35
```

**输出文件**：
- `corrected_subtitle.srt`：校正后的字幕，可直接导入剪辑软件
- `corrected_timestamped.txt`：校正后的时间戳文本

**参数调优**：
- `--ocr-interval` 默认 2.0s，唱歌视频可设为 `1.0` 提高时间精度
- `--ocr-bottom 0.35`：只扫画面底部 35%，避免医院标语等干扰
- 相似度阈值默认 `0.20`，可调但一般无需修改

### 5.10 唱歌视频 OCR-ASR 跨验证（v1.3.0+，独立工作流）

对唱歌/翻唱视频，`--ocr-correct` 的相似度阈值对齐可能漏掉歌词（时间窗口 ±2s 太宽导致跨段混入，或间隔太大漏掉画面文字）。
**跨验证工作流**用 0.1s 间隔密集采样 + 严格段内匹配，逐句从 OCR 画面文字中比对取歌词，精准率可达 95%+。

**核心原理**：
- **0.1s 间隔密集采样**：发现 1s/0.5s/0.3s 间隔全部漏掉的歌词（实测案例：段15/17 之前被判定"无 OCR"，0.1s 采样后清晰可见）
- **严格段内匹配**：`start_ms <= ts_ms < end_ms`（左闭右开），避免歌词跨段混入
- **噪声过滤**：医院招牌（禁止吸烟、主治医师等）、英文乱码、OCR 前缀噪声
- **质量分级**：high（OCR 直接匹配）/ low（无 OCR，ASR 校正）

**工作流脚本**（`scripts/cross_validate_ocr_asr.py`）：
```bash
# 对已有 SRT 字幕的视频做 OCR-ASR 跨验证
python "scripts/cross_validate_ocr_asr.py" <视频路径> <SRT路径> --ocr-interval 0.1

# 指定时间范围（只验证某段）
python "scripts/cross_validate_ocr_asr.py" <视频路径> <SRT路径> --range 43-50 --ocr-interval 0.1

# 输出 JSON + HTML 报告 + MD 歌词
python "scripts/cross_validate_ocr_asr.py" <视频路径> <SRT路径> --format all
```

**关键代码要点**：
1. **PaddleOCR 3.x API**：`predict()` 返回 list of dict，需 `for page in pred` 迭代：
   ```python
   pred = ocr.predict(str(img_path))
   for page in pred:
       if isinstance(page, dict):
           texts = page.get('rec_texts', [])
           scores = page.get('rec_scores', [])
           for text, score in zip(texts, scores):
               ...
   ```
2. **密集采样**：0.1s 间隔用 ffmpeg 逐帧提取（`-ss <时间> -i <视频> -frames:v 1`）
3. **噪声过滤**：
   ```python
   NOISE_KEYWORDS = ['禁止', '吸烟', 'NO SMOK', '主任', '医师', '医务', 'BP',
                    '出国师', '中西', '新结', '医码', '医销', '医国']
   NOISE_PREFIXES = ['禁止吸烟', '禁止吸煙', '医销', '医码', '出医码', '出银物', '出银销']
   ```
4. **严格段内匹配**：
   ```python
   def match_ocr(start_s, end_s):
       start_ms, end_ms = int(start_s * 1000), int(end_s * 1000)
       return [(ts, text) for ts, text in ocr_lyrics if start_ms <= ts < end_ms]
   ```

**输出产物**：
- `cross_validation_final.json`：完整验证数据（段号、时间、ASR 原音、最终歌词、OCR 匹配、质量）
- `cross_validation_report.html`：可视化对比报告
- `<主题>_v13.md`：Markdown 歌词（带时间戳 + ASR 原音 + OCR 画面对照）
- `<主题>_v13.txt`：纯歌词
- `<主题>_v13.srt`：SRT 字幕

### 5.11 ASR 术语自动修正 — 多领域智能检测（v1.3.5+）

Whisper 中文转写存在大量同音/近音错误（如"显起"→"险企"、"扁挑体"→"扁桃体"、"数居"→"数据"）。
技能内置术语替换机制，转写完成后**自动**检测领域并应用对应词典修正，无需手动干预。

**两阶段检测策略**（`_detect_domain`）：
1. **强信号 — wrong-form 扫描**：文本中出现任何领域的错误词（如"扁挑体"）→ 该领域命中。错误词是领域专属信号，不会误伤其他领域。
2. **弱信号 — 关键词匹配**（fallback）：短文本（<200字）阈值=1，长文本（≥200字）阈值=2。
3. **多领域叠加**：同一文本可匹配多个领域（如医学+生物），所有匹配领域的词典合并应用。

**内置词典**（`scripts/terminology/`）：

| 词典文件 | 领域 | 关键词数 | 错误词数 | 典型修正 |
|---------|------|---------|---------|---------|
| `common.json` | 通用 | — | 4 | 不象→不像, 以经→已经 |
| `finance.json` | 金融/保险 | 219 | 65 | 力插损→利差损, 显起→险企 |
| `medicine.json` | 医学 | 174 | 32 | 扁挑体→扁桃体, 心电体→心电图 |
| `biology.json` | 生物 | 203 | 38 | 蛋包质→蛋白质, 基音→基因 |
| `technology.json` | 科技/IT | 765 | 105 | 包错→报错, 线存→显存, 羊垃圾→洋垃圾 |
| `politics.json` | 政治/法律 | 289 | 31 | 法团→法院, 判绝→判决 |

**安全过滤**：所有替换项和关键词均强制 **≥2 字**（单字替换极易误伤，如"象→像"会把"大象"改成"大像"）。自指条目（wrong==right）自动过滤。关键词在加载时去重。

**使用方式**：
```bash
# 默认：自动检测领域 + 加载匹配词典
python "scripts/multi-media-processor.py" "<链接>" --model medium

# 指定自定义词典（跳过自动检测）
python "scripts/multi-media-processor.py" "<链接>" --terms ~/.workbuddy/terminology/myterms.json

# 词典格式（JSON）
# {
#   "name": "医学领域",
#   "description": "医学术语修正",
#   "keywords": ["患者", "医生", "医院", ...],   # 可选，用于领域检测
#   "replacements": {
#     "错误词": "正确词"
#   }
# }
```

**行为说明**：
- 转写完成后自动应用，日志输出 `[术语] 修正 N 处 | 领域: finance, medicine`
- 修正后的文本写回 `transcript_raw.txt`，下游 md/docx 均使用修正后文本
- 词典按词长降序排列，避免短词先匹配导致长词无法替换（如"行政丝讼"→"行政诉讼" 优先于 "丝讼"→"诉讼"）
- 词典结果按 mtime 缓存，避免重复 IO
- `--terms` 手动指定路径时跳过自动检测，仅加载指定词典

**安全机制（6 项设计级防护）**：

| 机制 | 实现位置 | 防护目标 |
|------|---------|---------|
| ≥2 字强制过滤 | `_load_dict()` / `_init_domain_dicts()` | 防止单字误替换（如"象→像"会把"大象"改成"大像"） |
| 词长降序排列 | `_load_dict()` | 防止短词先匹配破坏长词（如"行政丝讼"优先于"丝讼"） |
| 自指条目过滤 | `_load_dict()` | 跳过 `k==v` 无意义条目 |
| 领域专属 wrong-form | `_detect_domain()` Pass 1 | 错误词是领域专属信号（"扁挑体"只在 medicine 中） |
| 领域检测门控 | `_load_terminology()` | 只加载匹配领域词典，缩小误修正面 |
| 反向验证扫描 | `_post_correction_sanity_check()` | 替换后检测异常模式（重复字符/断裂词），只告警不修改 |

**反向验证（v1.3.2+）**：替换完成后扫描异常模式，只记录告警不修改文本：
- **重复字符**：检测"的的""是是""了了""在在""不不""有有""也也""都都"等中文中不应连续出现的字
- **断裂词**：检测"不的"等标准中文中不应出现的组合
- 告警日志格式：`[术语] 反向验证告警: 重复字符「是是」; 断裂词「不的」`
- 正常文本不产生告警，金融/医学/科技转写文本实测零误报

**明确的边界 — 不包含的功能**：
- ❌ **无「语境符合修正」**：替换完成后不会回过头做语境合理性检查（如"这个替换在上下文是否合理"）
- ❌ **无 LLM 语义校对**：全程不启用 LLM，纯本地规则式处理
- ✅ **反向验证扫描**：替换后检测异常模式（重复字符/断裂词），只告警不修改
- 系统依赖上述 6 项安全机制做**预防式设计 + 安全网**，而非修正后校验
- 如果需要额外的语境校对，应通过 `--ocr-correct`（OCR 画面验证）或手动审阅完成

**注意**：
- 术语修正是**规则替换**，不涉及语义理解，只做精确字符串匹配
- 如果词典中有误替换，编辑 JSON 文件删除对应条目即可
- 自定义词典建议存放在 `~/.workbuddy/terminology/` 目录下
- 关键词匹配可能存在少量跨领域误报（如"治疗"同时出现在医学和生物语境），但不会导致误修正——因为错误词是领域专属的

**实测案例**（《名字我早已想好》，1 分 36 秒翻唱视频）：
- 0.1s 间隔密集采样：331 帧（43-82s 范围）
- OCR 覆盖率：19/20 (95%)
- 修正 v12 的 6 处错误（段7/8/10/12/15/17）
- 唯一无 OCR 的段13（画面只有医院背景，无歌词字幕）

**实测案例**（《低价二手外星人内幕》，3 分钟硬件维修视频，v1.3.5 全量修正后）：
- 领域检测：Pass 1 wrong-form 命中 5 个错误词 → technology ✅
- 术语修正：16 类共 25 处（滑屏→花屏×4、包错→报错×4、线存→显存×2、羊垃圾→洋垃圾×2、无纤→无铅×2、机销商→经销商、黄布拉机的→黄不拉几的、就有系统→进系统、高温吸→高温锡、嘎嘎心→嘎嘎新、花瓶→花屏、平线→屏线、齐卷→起卷、充值→充新、专卖→专门、厚大→厚多）
- politics 误判修复：移除过于通用的关键词"死刑"（口语化用法"判死刑"误触发）
- BGAA bug 修复：移除"BG→BGA"替换（"BG"是"BGA"子串，替换后产生"BGAA"）
- **v1.3.5 语义章节划分实测**：204 句 SRT → 7 个一级核心章节 + 2 个二级小节（核心章节版参数：每40句一章），标题如「花屏检查与主板维修」「CPU组装与风枪焊接」「报错检查与BGA测试·失败」「细节检查与壳子更换·搞定」；旧版为 21 个每10句机械切分的「场景 N · 复述句」
- **v1.3.5 关键 bug 修复**：`_load_srt_segments` 只找 subtitle.srt 导致 sph 流程的 SRT 时间结构从未生效（实际走"每10句切一刀"回退）；现回退 `*_raw.srt`

**与 `--ocr-correct` 的区别**：
| 特性 | `--ocr-correct` | 跨验证工作流 |
|------|-----------------|--------------|
| OCR 间隔 | 默认 2s | 0.1s 密集 |
| 匹配策略 | 相似度阈值 | 严格段内比对取 |
| 质量标注 | 无 | high/low 分级 |
| 输出格式 | 覆盖原文件 | 独立新文件 |
| 适用场景 | 通用字幕校正 | 唱歌视频歌词精准校正 |
