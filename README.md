# 多媒体处理助手 (Multi-Media Processor)

> 微信视频号 / 公众号 / B站 / 抖音 / YouTube 等平台的视频下载与转写工具

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

## ⚙️ 配置说明

### Cookie 配置（必需）

首次使用时需要配置视频号 Cookie：

1. 访问 https://yuanbao.tencent.com 并登录
2. F12 → Application → Cookies → 复制 `.tencent.com` 域下的所有 Cookie
3. 编辑 `bin/wx_channels_download/config.yaml`，填入 `cloudflare.sphCookie`

### 可选配置（LLM 增强）

如需 LLM 智能整理，在环境变量中设置：
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
- httpx, yt-dlp, requests, zhconv, jieba

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

## 🐛 故障排查

| 问题 | 解决方案 |
|------|----------|
| 端口 2022 起不来 | 结束 `wx_video_download.exe` 进程后重跑 |
| 视频号报「no cookie」 | 更新 `config.yaml` 中的 `sphCookie` |
| 磁盘占用大 | 运行 `python scripts/install_dependencies.py --cleanup` |
| 识别准确度低 | 使用 `--model medium --denoise` 参数 |

## 📝 使用示例

```bash
# 高精度转写（专业术语/嘈杂环境）
python scripts/multi-media-processor.py "https://xxx" --model medium --denoise

# 只输出 Markdown
python scripts/multi-media-processor.py "https://xxx" --format md

# 生成爆款拆解分析
python scripts/multi-media-processor.py "https://xxx" --breakdown

# 只下载不转写
python scripts/multi-media-processor.py "https://xxx" --no-whisper
```

## 🔗 相关链接

- [原项目仓库](https://github.com/ltaoo/wx_channels_download)
- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/)

## 📄 许可证

MIT License
