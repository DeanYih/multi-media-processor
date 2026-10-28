# 多媒体处理助手 (Multi-Media Processor)

> 微信视频号 / 公众号 / 小鹅通课程 / B站 / 抖音 / YouTube 等平台的视频下载与转写工具
>
> 当前版本：**v1.4.0**

## 🚀 快速开始

```bash
# 安装技能
npx skills add deanyih/multi-media-processor

# 或使用 GitHub CLI
gh skill install deanyih/multi-media-processor
```

## 📋 功能特性

- **视频号解析**：`weixin.qq.com/sph/...` → 下载 → Whisper 转写
- **公众号文章**：`mp.weixin.qq.com/...` → 抓取正文 → 结构化摘要
- **小鹅通课程**：`*.h5.xet.pomoho.com/...` → 浏览器捕获视频流 → 分片枚举下载 → 拼接 → 转写
- **多平台支持**：B站、抖音、YouTube、TikTok、小红书、微博等
- **直接 m3u8**：给出播放列表地址 → ffmpeg 直读（自带 AES-128 解密）
- **本地文件**：支持 mp4/avi/mkv/mov/mp3/wav/flac 等常见格式
- **输出格式**：txt / srt / markdown / docx（四选一或全部）

### v1.4.0 新增能力

- **小鹅通课程视频下载**：课程页视频流带客户端签名 + `confusion_index` 混淆，纯 HTTP 抓不到。
  改为用 Playwright 驱动本机已装 Edge/Chrome 捕获流地址（扫码登录一次，profile 长期复用），
  然后**不解密、直接绕过分片枚举**——实测其分片签名不绑定路径，任意序号可取：
  指数探测 + 二分定边界 → 16 线程并发下载 → ffmpeg `-c copy` 无损拼接。
  实测 1 小时 43 分课程（1241 片 / 740 MB）23 秒下完，拼接 1 分钟。
- **直接 m3u8 入参**：`.m3u8` 链接无需走平台解析，ffmpeg 直读即可。
- **按 URL path 判定流类型**：修掉「KPI 上报接口 query 里带 m3u8 被误认为播放列表」的误判。

### v1.3.x 新增能力

- **ASR 术语自动修正**（v1.3.5+）：自动检测领域（金融/医学/生物/科技/政治法律/通用），加载对应词典修正 Whisper 同音错误，含 6 项安全防护与反向验证
- **分块语种检测**（v1.3.8+）：`--lang auto` 每 30 秒重判语种，中英混音视频必用，避免错误语种引发的复读幻觉
- **语义章节划分**（v1.3.5+）：TextTiling 式纯本地规则切章 + 实体动作概括标题，md/docx 按"两级章节 + 段落"排版
- **双语转写**（v1.3.1+）：`--bilingual` 英文段落自动附加中文翻译
- **OCR 校正 ASR**（v1.2.3+）：`--ocr-correct` 用画面歌词校正转写；视频帧 OCR（`--ocr`）支持底部区域限定、帧差异预筛提速
- **场景识别增强**（`--enhance`）：镜头检测 + 人声分离 + 音频事件标签
- **安全加固**：元宝 Cookie 走环境变量 `WX_SPH_COOKIE`（或 `~/.workbuddy/.secrets.env`）运行时注入，下载完成后自动清空 config.yaml 中的明文，密钥不落盘、不进仓库

## ⚙️ 配置说明

### Cookie 配置（必需）

视频号解析需要元宝 Cookie（约数周一换）：

1. 访问 https://yuanbao.tencent.com 并登录
2. F12 → Application → Cookies → 复制全部值拼成 `k=v;k=v` 字符串
3. 写入环境变量 `WX_SPH_COOKIE`（推荐 `~/.workbuddy/.secrets.env` 追加一行，**不要用 setx**——Cookie 超长会被 Windows 1024 字符上限截断）

技能运行时自动注入到 `config.yaml`，下载完成后自动清空明文。**请勿把含 Cookie 的 config.yaml 提交进仓库**（.gitignore 已排除）。

### 小鹅通课程（v1.4.0+，不需要 Cookie 字符串）

小鹅通不用 Cookie 字符串，而是用**真实浏览器登录态**：

```bash
# 首次会弹出浏览器窗口，扫码登录一次；之后登录态存在 profile 里长期复用
python scripts/multi-media-processor.py "<课程链接>"
```

- 依赖 `pip install playwright`（**不要**跑 `playwright install`，直接复用系统已装 Edge/Chrome）
- profile 目录默认 `~/.workbuddy/xet_profile`，可用 `--xet-profile` 或 `XET_PROFILE_DIR` 改
- 并发数默认 16，可用 `--xet-workers` 调
- 只下载不转写：加 `--no-whisper`

### 可选配置（LLM 增强）

如需 LLM 智能整理（默认不使用，全程本地规则引擎），在环境变量中设置：
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

## 🔧 系统要求

| 平台 | 状态 |
|------|------|
| Windows | ✅ 完整支持 |
| Linux | ✅ 完整支持 |
| macOS | ✅ 完整支持 |

## 📦 依赖说明

首次运行会自动下载以下依赖：
- ffmpeg (~142MB)
- Whisper CLI + 模型 (~253MB)
- wx_channels_download 工具 (~8MB)

Python 依赖（自动安装）：
- httpx, yt-dlp, requests, zhconv
- playwright —— 仅小鹅通分支需要（`xet_downloader.py` 按需导入，不装不影响其它平台），首次用需手动 `pip install playwright`

## 📄 输出示例

```
output/multi-media/video_title/
├── video_title.mp4          # 下载的视频
├── video_title_raw.txt      # 原始转写
├── video_title_subtitle.srt # 字幕文件
├── video_title_timestamped.txt  # 带时间戳文本
├── video_title.md           # Markdown 格式化
├── video_title.docx         # Word 文档
└── video_title_拆解.md      # 爆款拆解分析（可选）
```

## 📁 仓库结构

```
├── SKILL.md                            # 技能说明（v1.4.0 完整文档）
├── config.template.yaml                # 配置模板（无任何密钥）
└── scripts/
    ├── multi-media-processor.py        # 主脚本
    ├── xet_downloader.py               # 小鹅通流捕获 / 分片枚举下载（v1.4.0+）
    ├── scene_audio.py                  # 场景识别 / OCR / 音频事件模块
    ├── install_dependencies.py         # 依赖自动安装
    ├── cross_validate_ocr_asr.py       # OCR×ASR 跨验证（唱歌视频歌词精准校正）
    ├── terminology/                    # ASR 术语修正词典（6 领域）
    └── _deprecated/                    # 历史方案归档（sph Worker / Bridge）
```

## 🐛 故障排查

完整故障速查表见 [SKILL.md](SKILL.md) 第 4 节。常见问题：

| 问题 | 解决方案 |
|------|----------|
| 端口 2022 起不来 | 结束 `wx_video_download.exe` 进程后重跑 |
| 视频号报「no cookie」 | 重新抓取元宝 Cookie，覆盖 `WX_SPH_COOKIE` |
| 磁盘占用大 | 运行 `python scripts/install_dependencies.py --cleanup` |
| 识别准确度低 | 使用 `--model medium --denoise` 参数 |
| 中英混音视频转写出复读 | 使用 `--lang auto`（分块语种检测） |

## 📝 使用示例

```bash
# 高精度转写（专业术语/嘈杂环境）
python scripts/multi-media-processor.py "https://xxx" --model medium --denoise

# 只输出 Markdown
python scripts/multi-media-processor.py "https://xxx" --format md

# 中英混音视频
python scripts/multi-media-processor.py "https://xxx" --lang auto --format md

# 唱歌/歌词视频（OCR 为主 + 场景增强）
python scripts/multi-media-processor.py "https://xxx" --enhance --ocr --ocr-correct --ocr-bottom 0.35

# 只下载不转写
python scripts/multi-media-processor.py "https://xxx" --no-whisper
```

## 🔗 相关链接

- [原项目仓库](https://github.com/ltaoo/wx_channels_download)
- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/)

## 📄 许可证

MIT License
