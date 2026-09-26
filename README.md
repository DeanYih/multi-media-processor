# 多媒体处理助手 (Multi-Media Processor)

> 微信视频号 / 公众号 / B站 / 抖音 / YouTube 等平台的视频下载与转写工具
>
> 当前版本：**v1.3.8**

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
- **多平台支持**：B站、抖音、YouTube、TikTok、小红书、微博等
- **本地文件**：支持 mp4/avi/mkv/mov/mp3/wav/flac 等常见格式
- **输出格式**：txt / srt / markdown / docx（四选一或全部）

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
├── SKILL.md                            # 技能说明（v1.3.8 完整文档）
├── config.template.yaml                # 配置模板（无任何密钥）
└── scripts/
    ├── multi-media-processor.py        # 主脚本
    ├── scene_audio.py                  # 场景识别 / OCR / 音频事件模块
    ├── install_dependencies.py         # 依赖自动安装
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
