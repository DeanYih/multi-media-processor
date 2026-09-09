#!/usr/bin/env python3
"""
multi-media-processor 依赖安装脚本
自动检测系统平台并下载对应依赖
用法: python scripts/install_dependencies.py [--skip-ffmpeg] [--check-updates]
"""
import sys
import os
import re
import shutil
import platform
from pathlib import Path
import hashlib
import urllib.request
import ssl
import json
import zipfile
import tarfile
import time
import tempfile

# 技能根目录
SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
BIN_DIR = SKILL_DIR / "bin"
TEMP_DIR = SKILL_DIR / ".temp_downloads"

# ========== 版本配置 ==========
VERSIONS = {
    "wx_channels_download": "v260817",
    "whisper_cli": "b4938",
    "ffmpeg": "7.1",
    "whisper_model": "v1.5.0",
    "yt_dlp": "2026.08.19",
}

# 模型文件映射（用于按需下载）
MODEL_FILES = {
    "tiny":  {"filename": "ggml-tiny.bin", "min_size_mb": 100},
    "base": {"filename": "ggml-base.bin", "min_size_mb": 800},
    "small": {"filename": "ggml-small-q8_0.bin", "min_size_mb": 200},
    "medium": {"filename": "ggml-medium.bin", "min_size_mb": 500},
}

# ========== 依赖来源链接（优先国内镜像）==========
DEPENDENCY_SOURCES = {
    "wx_channels_download": "https://github.com/ltaoo/wx_channels_download",
    "whisper_cli": "https://github.com/ggml-org/whisper.cpp",
    "ffmpeg": "https://www.gyan.dev/ffmpeg/builds/",
    "whisper_model": "https://github.com/ggerganov/whisper.cpp",
    "yt_dlp": "https://github.com/yt-dlp/yt-dlp",
}

# ========== 文件哈希验证（安全哈希指纹）==========
EXPECTED_HASHES = {
    # wx_video_download.exe (Windows x86_64) - v260817
    "wx_video_download.exe": "be7fa37280f0223288a03c7e976be426",
}

# ========== 国内镜像加速配置 ==========
MIRROR_BASE = "https://ghfast.top/"
HF_MIRROR = "https://hf-mirror.com/"

def calculate_file_hash(filepath, algorithm='md5'):
    """计算文件哈希值用于完整性验证"""
    try:
        h = hashlib.new(algorithm)
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print(f"[哈希] 计算失败: {e}")
        return None

def verify_file_integrity(filepath, expected_hash=None):
    """验证文件完整性，返回 (verified, actual_hash, expected_hash)"""
    if not filepath.exists():
        return False, None, expected_hash

    actual_hash = calculate_file_hash(filepath)
    if expected_hash and actual_hash != expected_hash:
        print(f"[验证] 警告: {filepath.name} 哈希不匹配")
        print(f"  预期: {expected_hash}")
        print(f"  实际: {actual_hash}")
        return False, actual_hash, expected_hash

    print(f"[验证] ✅ {filepath.name} 完整性验证通过")
    return True, actual_hash, expected_hash

def fetch_latest_release(repo):
    """从 GitHub API 获取最新版本信息"""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'multi-media-processor/1.0',
            'Accept': 'application/json'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return {
                'tag_name': data.get('tag_name', ''),
                'assets': [{
                    'name': asset['name'],
                    'browser_download_url': asset['browser_download_url'],
                } for asset in data.get('assets', [])]
            }
    except Exception as e:
        print(f"[版本检查] 无法获取 {repo} 最新版本: {e}")
        return None

def check_for_updates():
    """检查是否有可用的依赖更新"""
    print("\n[版本检查] 正在检查最新依赖版本...")

    updates = []

    # 检查 wx_channels_download
    release = fetch_latest_release("ltaoo/wx_channels_download")
    if release:
        current = VERSIONS["wx_channels_download"]
        latest = release['tag_name']
        if latest > current:
            updates.append({
                "name": "wx_channels_download",
                "current": current,
                "latest": latest,
                "url": release['assets'][0]['browser_download_url'] if release['assets'] else None
            })

    # 检查 whisper.cpp
    release = fetch_latest_release("ggml-org/whisper.cpp")
    if release:
        current = VERSIONS["whisper_cli"]
        latest = release['tag_name']
        if latest > current:
            updates.append({
                "name": "whisper.cpp",
                "current": current,
                "latest": latest,
                "url": release['assets'][0]['browser_download_url'] if release['assets'] else None
            })

    # 检查 yt-dlp
    try:
        import yt_dlp
        current = VERSIONS["yt_dlp"]
        latest = yt_dlp.version.__version__
        if latest > current:
            updates.append({
                "name": "yt-dlp",
                "current": current,
                "latest": latest,
                "url": DEPENDENCY_SOURCES["yt_dlp"]
            })
    except ImportError:
        updates.append({
            "name": "yt-dlp",
            "current": "未安装",
            "latest": VERSIONS["yt_dlp"],
            "url": DEPENDENCY_SOURCES["yt_dlp"]
        })

    if updates:
        print("\n[更新可用]")
        for u in updates:
            print(f"  {u['name']}: {u['current']} → {u['latest']}")
        return updates
    else:
        print("[版本检查] 所有依赖已是最新版本")
        return []

# ========== 系统检测 ==========
def get_platform_info():
    """检测系统平台和架构"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        return "windows", machine
    elif system == "linux":
        return "linux", machine
    elif system == "darwin":
        return "macos", machine
    else:
        return system, machine

PLATFORM, ARCH = get_platform_info()
IS_WINDOWS = PLATFORM == "windows"
IS_LINUX = PLATFORM == "linux"
IS_MACOS = PLATFORM == "macos"

print(f"[系统] {PLATFORM.upper()} {ARCH}", flush=True)

# ========== 平台化依赖定义 ==========
if IS_WINDOWS:
    EXE_EXT = ".exe"
    BIN_PKG = "whisper-bin-x64"
    WHISPER_BIN_SUBDIR = "bin"
    FFMPEG_SUBDIR = f"ffmpeg-{VERSIONS['ffmpeg']}-full_build/bin"
elif IS_LINUX:
    EXE_EXT = ""
    BIN_PKG = "whisper-bin-x64"
    WHISPER_BIN_SUBDIR = "bin"
    FFMPEG_SUBDIR = f"ffmpeg-{VERSIONS['ffmpeg']}-full_build/bin"
elif IS_MACOS:
    EXE_EXT = ""
    BIN_PKG = "whisper-bin-x64" if "x86" in ARCH else "whisper-bin-arm64"
    WHISPER_BIN_SUBDIR = "bin"
    FFMPEG_SUBDIR = f"ffmpeg-{VERSIONS['ffmpeg']}-full_build/libexec/ffmpeg"
else:
    EXE_EXT = ""
    BIN_PKG = "whisper-bin-x64"
    WHISPER_BIN_SUBDIR = "bin"
    FFMPEG_SUBDIR = f"ffmpeg-{VERSIONS['ffmpeg']}-full_build/bin"

def _get_channels_download_info():
    """根据系统返回下载器信息和文件名（全平台支持）"""
    version = VERSIONS["wx_channels_download"]
    base_url = f"https://github.com/ltaoo/wx_channels_download/releases/download/{version}"

    if IS_WINDOWS:
        arch_suffix = "arm64" if ("arm" in ARCH or "aarch64" in ARCH) else "x86_64"
        return {
            "name": f"wx_channels_download ({PLATFORM} {ARCH})",
            "path": BIN_DIR / "wx_channels_download" / "wx_video_download.exe",
            "urls": [
                f"{MIRROR_BASE}{base_url}/wx_video_download_safe_{version}_windows_{arch_suffix}.zip",
                f"{base_url}/wx_video_download_safe_{version}_windows_{arch_suffix}.zip",
            ],
            "extract_name": None,  # zip 内无外层目录，解压后递归查找
            "min_size_mb": 8,
        }
    elif IS_MACOS:
        arch_suffix = "arm64" if ("arm" in ARCH or "aarch64" in ARCH) else "x86_64"
        return {
            "name": f"wx_channels_download ({PLATFORM} {ARCH})",
            "path": BIN_DIR / "wx_channels_download" / "wx_video_download",
            "urls": [
                f"{MIRROR_BASE}{base_url}/wx_video_download_{version}_darwin_{arch_suffix}.zip",
                f"{base_url}/wx_video_download_{version}_darwin_{arch_suffix}.zip",
            ],
            "extract_name": None,  # 解压后递归查找
            "min_size_mb": 8,
        }
    elif IS_LINUX:
        arch_suffix = "arm64" if ("arm" in ARCH or "aarch64" in ARCH) else "x86_64"
        return {
            "name": f"wx_channels_download ({PLATFORM} {ARCH})",
            "path": BIN_DIR / "wx_channels_download" / "wx_video_download",
            "urls": [
                f"{MIRROR_BASE}{base_url}/wx_video_download_{version}_linux_{arch_suffix}.tar.gz",
                f"{base_url}/wx_video_download_{version}_linux_{arch_suffix}.tar.gz",
            ],
            "extract_name": None,  # 解压后递归查找
            "min_size_mb": 8,
        }
    return None

# 依赖项定义（平台自适应）
DEPENDENCIES = [
    {
        "name": "Whisper 小模型 (ggml-small-q8_0.bin)",
        "path": BIN_DIR / "whisper" / "models" / "ggml-small-q8_0.bin",
        "min_size_mb": 200,
        "urls": [
            # 优先国内镜像
            f"{MIRROR_BASE}https://github.com/ggerganov/whisper.cpp/releases/download/{VERSIONS['whisper_model']}/ggml-small-q8_0.bin",
            f"{HF_MIRROR}ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin",
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin",
        ],
        "hash": EXPECTED_HASHES.get("ggml-small-q8_0.bin"),
    },
    {
        "name": f"ffmpeg{EXE_EXT}",
        "path": BIN_DIR / "whisper" / "bin" / f"ffmpeg{EXE_EXT}",
        "min_size_mb": 50,
        "urls": [
            # 优先国内镜像
            f"{MIRROR_BASE}https://github.com/GyanD/codexffmpeg/releases/download/{VERSIONS['ffmpeg']}/ffmpeg-{VERSIONS['ffmpeg']}-full_build.zip",
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        ],
        "is_archive": True,
        "extract_path": BIN_DIR / "whisper" / "bin",
        "extract_name": None,  # 不指定内部路径，让代码递归查找 ffmpeg.exe
        # 修复(2026-08-26): 解压后清残留的完整 build 目录（数百MB 磁盘占用）
        "cleanup_globs": ["ffmpeg-*-full_build", "ffmpeg-*-essentials_build"],
    },
    {
        "name": "Whisper CLI 引擎",
        "path": BIN_DIR / "whisper" / "bin" / f"whisper-cli{EXE_EXT}",
        # 修复(2026-08-26): whisper-cli.exe 本体仅 ~0.46MB（dll 才是大头），原 min_size_mb=1 会误判为不完整反复触发下载
        "min_size_mb": 0.4,
        "urls": [
            # 优先国内镜像
            f"{MIRROR_BASE}https://github.com/ggml-org/whisper.cpp/releases/download/{VERSIONS['whisper_cli']}/{BIN_PKG}.zip",
            f"https://github.com/ggml-org/whisper.cpp/releases/download/{VERSIONS['whisper_cli']}/{BIN_PKG}.zip",
        ],
        "is_archive": True,
        "extract_path": BIN_DIR / "whisper" / "bin",
        # 修复(2026-08-26): zip 内 dll 位于 Release/ 子目录，exe 运行要求 dll 同级
        # → 解压后自动拍平到 bin 根目录；requires_files 用于检测旧安装缺 dll 时触发修复安装
        "flatten_dlls": True,
        "requires_files": ["whisper.dll", "ggml.dll"],
        "cleanup_globs": ["Release"],
    },
]

# 微信视频号下载器 - 全平台支持
channels_dep = _get_channels_download_info()
if channels_dep:
    DEPENDENCIES.append({
        **channels_dep,
        "is_archive": True,
        "extract_path": BIN_DIR / "wx_channels_download",
        "hash": EXPECTED_HASHES.get("wx_video_download.exe"),
        # 修复(2026-08-26): 安装后强制预置关闭 config.yaml 的 proxy 段
        # （工具默认 proxy.system 会劫持 Windows 系统代理 → 弹黑窗/断网事故根源）
        "patch_config": True,
    })

# ========== 下载工具函数 ==========
def download_file(url, dest, timeout=300):
    """下载文件并返回是否成功，支持断点续传和指数退避重试。

    修复(2026-08-26):
      ① SSL 安全：默认走证书验证，仅当证书校验失败时对该请求降级为不校验
         （原实现全局禁用证书校验，属安全隐患；部分镜像源证书异常时仍可用）
      ② 断点续传：服务端忽略 Range 请求返回 200 时自动改为覆盖写，
         原实现继续追加会导致文件损坏（下载100%但解压失败的诱因之一）
    """
    import socket as _socket

    def _open_url(req):
        """优先走默认证书验证；证书校验失败时降级为不校验（兼容部分镜像源）"""
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                print("  [SSL] 证书校验失败，降级为不校验重试（镜像源兼容）")
                ctx = ssl._create_unverified_context()
                return urllib.request.urlopen(req, timeout=timeout, context=ctx)
            raise

    def _do_download():
        req = urllib.request.Request(url, headers={
            'User-Agent': 'multi-media-processor/1.0',
            'Accept-Encoding': 'gzip, deflate'
        })

        # 断点续传：跳过已下载部分
        downloaded = 0
        if dest.exists():
            downloaded = dest.stat().st_size
            if downloaded > 0:
                req.headers['Range'] = f'bytes={downloaded}-'

        with _open_url(req) as resp:
            # 修复: 服务端忽略 Range 返回 200（而非 206）时，覆盖写而非追加
            resume_ok = getattr(resp, "status", 200) == 206
            if downloaded > 0 and not resume_ok:
                downloaded = 0
            total_header = resp.headers.get('Content-Length', '0')
            total = int(total_header)
            if downloaded > 0 and total > 0:
                total += downloaded  # 续传时加上已有大小
            chunk_size = 131072  # 128KB 块
            mode = 'ab' if downloaded > 0 else 'wb'
            with open(dest, mode) as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 / total
                        print(f"\r  进度: {pct:.1f}% ({downloaded//1024//1024}MB/{total//1024//1024}MB)", end='', flush=True)
        print()  # 换行
        return True

    # 指数退避重试（最多5次），避免网络抖动导致失败
    for retry in range(5):
        try:
            _do_download()
            return True
        except (urllib.error.HTTPError, urllib.error.URLError,
                _socket.timeout, _socket.error, OSError, ConnectionError,
                KeyboardInterrupt) as e:
            if retry < 4:
                wait = min(2 ** retry, 30)  # 1s, 2s, 4s, 8s, 最大30s
                print(f"\r  重试 {retry+1}/5 ({wait}s后)...", end='', flush=True)
                time.sleep(wait)
                # 删除不完整文件，下次从头下载
                if dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
            else:
                print(f"\n  下载失败（已重试5次）: {e}")
                return False
    return False

def extract_archive(archive_path, extract_path, extract_name=None):
    """解压压缩包（按文件内容识别格式，不依赖扩展名）"""
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as zf:
                if extract_name:
                    # 只提取指定文件
                    zf.extract(extract_name, extract_path)
                else:
                    zf.extractall(extract_path)
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, 'r:*') as tf:
                if extract_name:
                    # 找到匹配的文件
                    for member in tf.getmembers():
                        if extract_name in member.name:
                            tf.extract(member, extract_path)
                            break
                else:
                    tf.extractall(extract_path)
        else:
            print(f"  无法识别的压缩包格式: {archive_path.name}")
            return False
        return True
    except Exception as e:
        print(f"  解压失败: {e}")
        return False

def find_extracted_file(extract_path, target_name):
    """在解压目录中递归查找目标文件"""
    # 首先尝试直接匹配
    direct_match = extract_path / target_name
    if direct_match.exists():
        return direct_match

    # 递归查找
    matches = list(extract_path.rglob(target_name))
    if matches:
        return matches[0]

    # 尝试查找包含 target_name 的文件
    for f in extract_path.rglob('*'):
        if f.name == target_name:
            return f

    return None


def _safe_unlink(path):
    """删除文件，失败仅告警不影响主流程。
    修复(2026-08-26): 临时文件清理失败（如沙箱无回收站/文件被占用）
    不得影响安装成功判定（原实现可能因清理失败连带判安装失败）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        print(f"  [清理] 临时文件删除失败（忽略）: {e}")


def flatten_runtime_dlls(extract_path):
    """把解压目录子文件夹里的 dll 拍平到根目录。

    修复(2026-08-26): whisper-bin-x64.zip 的 dll 位于 Release/ 子目录，
    whisper-cli.exe 运行时要求 dll 与 exe 同级，否则报缺 dll 直接退出。
    安装器自动拍平，用户无需手动复制。"""
    moved = 0
    try:
        for sub in extract_path.iterdir():
            if sub.is_dir():
                for dll in sub.rglob("*.dll"):
                    dest = extract_path / dll.name
                    if not dest.exists():
                        shutil.move(str(dll), str(dest))
                        moved += 1
    except Exception as e:
        print(f"  [dll拍平] 跳过（不影响安装）: {e}")
    if moved:
        print(f"  [dll拍平] 已将 {moved} 个 dll 移至 bin 根目录（whisper-cli 运行必需）")
    return moved


def patch_tool_config_disable_proxy(cfg_path):
    """把 wx_channels_download 的 config.yaml 中 proxy 段强制改为关闭。

    修复(2026-08-26): 工具默认 proxy.enabled+system=true 会把 Windows 系统
    代理指向 127.0.0.1:2023 —— Go 程序调 netsh 每次闪 CMD 黑窗，且工具被
    强杀时代理设置残留导致全系统断网。视频号解析走 sphCookie 方案无需代理，
    安装时强制预置关闭。仅按行改 enabled/system 两个键，不触碰 sphCookie。
    返回 True 表示有修改并已写回。"""
    cfg = Path(cfg_path)
    if not cfg.exists():
        return False
    try:
        lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as e:
        print(f"  [安全网] 读取 config.yaml 失败: {e}")
        return False
    in_proxy, changed = False, False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if line and not line[0].isspace() and stripped and not stripped.startswith("#"):
            # 顶格键：进入/退出 proxy 段
            in_proxy = stripped.split(":")[0].strip() == "proxy"
            continue
        if not in_proxy:
            continue
        m = re.match(r"(\s*)(enabled|system)(\s*:\s*)(true|True|YES|yes|on)\b", line)
        if m:
            tail = "\n" if line.endswith("\n") else ""
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}false{tail}"
            changed = True
    if changed:
        try:
            cfg.write_text("".join(lines), encoding="utf-8")
            print("  [安全网] 已预置关闭 config.yaml 的 proxy（防系统代理劫持/黑窗/断网）")
        except Exception as e:
            print(f"  [安全网] 写回 config.yaml 失败: {e}")
    return changed


def cleanup_extract_leftovers(extract_path, dep):
    """清理解压残留的子目录（磁盘瘦身）。

    修复(2026-08-26): ffmpeg 解压后残留 ffmpeg-*_build 完整目录（约数百MB）、
    whisper 拍平后残留 Release/ 目录，长期占用磁盘。清理失败仅告警，
    绝不影响安装结果（fail-open）。"""
    patterns = dep.get("cleanup_globs", [])
    for pat in patterns:
        try:
            for d in extract_path.glob(pat):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                    print(f"  [清理] 已删除解压残留目录: {d.name}（磁盘瘦身）")
        except Exception as e:
            print(f"  [清理] 跳过 {pat}（不影响安装）: {e}")

def install_dependency(dep):
    """安装单个依赖"""
    name = dep["name"]
    target = dep["path"]
    target_name = target.name

    # 检查是否已存在且有效
    if target.exists():
        size_mb = target.stat().st_size / (1024 * 1024)
        min_size = dep.get("min_size_mb", 0)
        # 修复(2026-08-26): 伴随文件检查（如 whisper-cli 需要 whisper.dll 同级，
        # 旧版安装缺 dll 时自动触发修复安装而非静默放行）
        missing_extras = [e for e in dep.get("requires_files", [])
                          if not (target.parent / e).exists()]
        if size_mb >= min_size and not missing_extras:
            # 验证完整性
            if dep.get("hash"):
                verified, _, _ = verify_file_integrity(target, dep["hash"])
                if verified:
                    print(f"[已安装] {name} ✅")
                    return True
            else:
                print(f"[已安装] {name} ({size_mb:.1f}MB) ✅")
                return True
        elif missing_extras:
            print(f"[修复] {name} 缺少伴随文件: {', '.join(missing_extras)}，重新安装修复...")

    print(f"\n[安装] {name}...")

    # 创建临时目录
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # 尝试多个源（优先使用国内镜像）
    urls = dep.get("urls", [])
    for i, url in enumerate(urls):
        print(f"  源 {i+1}/{len(urls)}: 尝试下载...")

        # 使用唯一临时文件名
        tmp_filename = f"__mmp_tmp_{int(time.time()*1000)}_{target_name}.tmp"
        dest = TEMP_DIR / tmp_filename

        try:
            if download_file(url, dest):
                # 验证文件大小
                file_size = dest.stat().st_size
                if file_size < 1024 * 1024:  # 小于 1MB 可能是错误页面
                    print(f"  ⚠️ 文件过小 ({file_size//1024}KB)，跳过")
                    _safe_unlink(dest)
                    continue

                # 解压（如果是压缩包）
                if dep.get("is_archive"):
                    extract_path = Path(dep.get("extract_path", target.parent))
                    extract_path.mkdir(parents=True, exist_ok=True)

                    if extract_archive(dest, extract_path, dep.get("extract_name")):
                        # 查找解压后的文件
                        extracted_file = find_extracted_file(extract_path, target_name)

                        if extracted_file and extracted_file.exists():
                            # 移动到目标位置（src 与 dst 为同一路径时跳过，避免 move 报错）
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if extracted_file.resolve() != target.resolve():
                                shutil.move(str(extracted_file), str(target))
                            # 修复(2026-08-26): 拍平子目录 dll（whisper-cli 的 dll 在 Release/ 下）
                            if dep.get("flatten_dlls"):
                                flatten_runtime_dlls(extract_path)
                            # 修复(2026-08-26): 预置关闭工具代理（防系统代理劫持/黑窗/断网）
                            if dep.get("patch_config"):
                                patch_tool_config_disable_proxy(extract_path / "config.yaml")
                            # 修复(2026-08-26): 清理解压残留目录（磁盘瘦身，失败不影响安装）
                            cleanup_extract_leftovers(extract_path, dep)
                            print(f"[完成] {name} ✅")
                            # 清理临时文件（失败不影响安装结果）
                            _safe_unlink(dest)
                            return True
                        else:
                            print(f"  ⚠️ 未找到解压后的文件: {target_name}")

                else:
                    # 直接文件，移动
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dest), str(target))
                    print(f"[完成] {name} ✅")
                    return True
            else:
                print(f"  ❌ 下载失败")
        except Exception as e:
            print(f"  ❌ 异常: {e}")
        finally:
            # 确保清理临时文件（失败仅告警，不推翻安装结果）
            if dest.exists():
                _safe_unlink(dest)

        # 非最后一个 URL 失败后，提示并继续
        if i < len(urls) - 1:
            print(f"  尝试下一个源...")

    print(f"[失败] {name} - 所有下载源均失败")
    return False

def check_dependency(dep):
    """检查单个依赖是否已安装"""
    target = dep["path"]
    if not target.exists():
        return False
    size_mb = target.stat().st_size / (1024 * 1024)
    min_size = dep.get("min_size_mb", 0)
    if size_mb < min_size:
        return False
    # 修复(2026-08-26): 伴随文件检查（whisper-cli 缺 dll 时判为不完整）
    for extra in dep.get("requires_files", []):
        if not (target.parent / extra).exists():
            print(f"[依赖] {target.name} 缺少伴随文件 {extra}，将触发修复安装")
            return False
    if dep.get("hash"):
        verified, _, _ = verify_file_integrity(target, dep["hash"])
        return verified
    return True


def ensure_deps(dep_names):
    """确保指定名称的依赖都已安装，批量安装缺失项（并行下载）"""
    needed = [d for d in DEPENDENCIES if d["name"] in dep_names or d["path"].name in dep_names]
    missing = [d for d in needed if not check_dependency(d)]
    if not missing:
        print("[依赖] 所有依赖已就绪 ✅")
        return True
    print(f"[依赖] 检测到 {len(missing)} 个依赖缺失，开始并行下载...", flush=True)

    # 并行下载，max_workers=4 避免同时拉太多连接拖垮网络
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_name = {pool.submit(install_dependency, d): d["name"] for d in missing}
        for future in as_completed(future_to_name, timeout=1800):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as e:
                print(f"  [异常] {name}: {e}")
                results[name] = False

    success_count = sum(1 for v in results.values() if v)
    total = len(missing)
    print(f"[依赖] 下载完成: {success_count}/{total} 成功")

    if success_count == total:
        return True
    elif success_count > 0:
        print(f"[依赖] 部分成功，已安装的依赖可用，缺失项: {[n for n, ok in results.items() if not ok]}")
        return True
    return False

# 清理临时文件的函数
def cleanup_temp():
    """清理临时下载目录"""
    if TEMP_DIR.exists():
        try:
            shutil.rmtree(TEMP_DIR)
            return True
        except:
            pass
    return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="安装 multi-media-processor 依赖")
    parser.add_argument("--skip-ffmpeg", action="store_true", help="跳过 ffmpeg 安装")
    parser.add_argument("--check-updates", action="store_true", help="检查是否有新版本")
    parser.add_argument("--cleanup", action="store_true", help="清理临时文件")
    args = parser.parse_args()

    if args.cleanup:
        if cleanup_temp():
            print("✅ 临时文件已清理")
        sys.exit(0)

    if args.check_updates:
        updates = check_for_updates()
        if updates:
            print("\n请手动更新 VERSIONS 字典中的版本号后重新安装")
        sys.exit(0 if not updates else 1)

    # 安装所有依赖（互不依赖的项目并行下载，避免串行等待）
    from concurrent.futures import ThreadPoolExecutor
    to_install = [d for d in DEPENDENCIES
                  if not (args.skip_ffmpeg and "ffmpeg" in d["name"].lower())]
    if len(to_install) > 1:
        print(f"\n[并行] 并发下载 {len(to_install)} 个依赖...", flush=True)
        with ThreadPoolExecutor(max_workers=len(to_install)) as pool:
            results = list(pool.map(install_dependency, to_install))
        success_count = sum(1 for r in results if r)
    else:
        success_count = 0
        for dep in to_install:
            if install_dependency(dep):
                success_count += 1

    print(f"\n安装完成: {success_count}/{len(DEPENDENCIES)} 个依赖")

    # 清理临时文件
    cleanup_temp()

    # 生成哈希文件供后续验证
    hash_file = SKILL_DIR / ".dependency_hashes.json"
    hashes = {}
    for dep in DEPENDENCIES:
        if dep["path"].exists():
            h = calculate_file_hash(dep["path"])
            if h:
                hashes[dep["path"].name] = h

    if hashes:
        with open(hash_file, 'w') as f:
            json.dump(hashes, f, indent=2)
        print(f"\n[哈希] 已记录 {len(hashes)} 个文件的哈希值")
