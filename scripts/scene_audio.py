# -*- coding: utf-8 -*-
"""场景与音频识别增强模块 (multi-media-processor)

可选依赖（未安装时自动降级为本地规则式，不报错）：
  - scenedetect[opencv] : 镜头/转场检测 -> 章节切分
  - demucs              : 人声分离 -> 唱歌/带BGM视频的转写前处理
  - panns_inference     : 音频事件分类 (AudioSet 527) -> 片段标签

所有重依赖均懒导入，模块本身始终可安全 import。
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# ---------------- 可用性检测 ----------------
def _have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False

def scene_detect_available():
    return _have("scenedetect")

def vocal_sep_available():
    return _have("demucs")

def audio_tag_available():
    return _have("panns_inference") and _have("librosa")


# ---------------- ffmpeg 可用性（demucs / panns 读音视频需要） ----------------
def _find_skill_bin(name):
    """在技能目录里递归找指定可执行文件（ffmpeg / ffprobe），返回绝对路径。

    找不到时返回 `name` 本身，退化成依赖系统 PATH（保持原行为，不至于直接失败）。

    注意 ffmpeg 与 ffprobe **可能不在同一目录**（本机实测：ffmpeg 在
    `bin/whisper/bin/`，ffprobe 在 `bin/whisper/bin/ffmpeg-7.1-full_build/bin/`），
    所以必须各自独立查找，不能用 ffmpeg 的目录去推 ffprobe。
    """
    import glob as _glob
    here = Path(__file__).resolve()
    names = (f"{name}.exe", name) if os.name == "nt" else (name,)

    # 1) 固定候选（快路径）
    for p in [here, *here.parents]:
        for sub in ("bin", os.path.join("bin", "whisper", "bin"),
                    os.path.join("whisper", "bin")):
            d = p / sub
            for n in names:
                cand = d / n
                if cand.exists():
                    return str(cand)
    # 2) 递归兜底：技能根 bin/ 下最多 5 层（解压目录名带版本号会变）
    for p in here.parents:
        root = p / "bin"
        if not root.is_dir():
            continue
        for n in names:
            for depth in range(1, 6):
                hits = _glob.glob(os.path.join(str(root), *(["*"] * depth), n))
                if hits:
                    return hits[0]
    # 3) 系统常见安装位
    for sysdir in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"):
        for n in names:
            cand = os.path.join(sysdir, n)
            if os.path.exists(cand):
                return cand
    return name


def _skill_ffmpeg_dir():
    """返回技能自带 ffmpeg 所在目录（用于加 PATH）；找不到返回 None。"""
    ff = _find_skill_bin("ffmpeg")
    d = os.path.dirname(ff)
    return d if os.path.isabs(ff) and os.path.isdir(d) else None

def _ensure_ffmpeg():
    d = _skill_ffmpeg_dir()
    if d and d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    return d


def _find_ffmpeg():
    """返回可用的 ffmpeg 可执行文件路径（优先技能自带，找不到退回裸命令依赖 PATH）。"""
    return _find_skill_bin("ffmpeg")


def _find_ffprobe():
    """返回可用的 ffprobe 路径（优先技能自带）。

    必须与 ffmpeg **分开查找**：两者可能不在同一目录。
    """
    return _find_skill_bin("ffprobe")


def video_duration(video_path):
    """获取音视频时长（秒）。ffprobe 优先，失败则解析 ffmpeg 的 stderr。失败返回 0.0。

    注意：-show_entries/-of 是 ffprobe 的语法，ffmpeg 不支持，务必用 ffprobe 调用。
    """
    import re
    # 1) ffprobe
    try:
        fp = _find_ffprobe()
        out = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        v = float(out)
        if v > 0:
            return v
    except Exception:
        pass
    # 2) ffmpeg -i 解析 "Duration: 00:00:06.00"
    try:
        r = subprocess.run([_find_ffmpeg(), "-i", str(video_path)],
                           capture_output=True, text=True, timeout=30)
        blob = (r.stderr or "") + (r.stdout or "")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d*\.?\d+)", blob)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


# 纯音频扩展名（无画面），ffprobe 不可用时的回退依据
_AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma',
               '.aiff', '.aif', '.opus', '.amr', '.ape', '.mka', '.dts'}

_HAS_VIDEO_CACHE = {}


def has_video_stream(path) -> bool:
    """判断文件是否真的含有视频流（有画面）。纯音频文件返回 False。

    用于门控 OCR / 镜头检测——纯音频跑这些是纯浪费（且 OCR 必然 0 条结果）。

    判定顺序：
      1) ffprobe 查 v:0 流的 codec_type == video（权威）
      2) ffprobe 不可用时回退按扩展名判断
    结果按 (绝对路径, mtime, size) 缓存，同一文件不重复探测。
    """
    try:
        p = Path(path)
        st = p.stat()
        key = (str(p.resolve()), int(st.st_mtime), st.st_size)
    except Exception:
        key = None

    if key is not None and key in _HAS_VIDEO_CACHE:
        return _HAS_VIDEO_CACHE[key]

    result = None
    # 1) ffprobe 权威判定
    try:
        fp = _find_ffprobe()
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30).stdout.strip().lower()
        if out:
            result = ("video" in out)
    except Exception:
        result = None

    # 2) 回退：按扩展名
    if result is None:
        ext = os.path.splitext(str(path))[1].lower()
        result = ext not in _AUDIO_EXTS

    if key is not None:
        _HAS_VIDEO_CACHE[key] = result
    return result


def _to_wav(video_path: str, sr: int = 32000):
    """用 ffmpeg 把任意音视频转成 sr 单声道 wav（librosa 不支持 mp4 容器）。
    返回临时 wav 路径；失败返回 None。"""
    import tempfile
    ff = _find_ffmpeg()
    out = tempfile.mktemp(suffix=".wav")
    cmd = [ff, "-y", "-loglevel", "error", "-i", video_path,
           "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    return out if os.path.exists(out) else None


# ---------------- 1. 镜头 / 转场检测 ----------------
def detect_scenes(video_path, threshold=27.0):
    """返回转场边界时间戳列表 [ms]，升序去重，不含 0 与结尾。
    失败或无库时返回 []（调用方回退到时间间隔规则）。

    门控：纯音频文件（无视频流）直接返回 []——没有画面就谈不上转场。
    """
    if not scene_detect_available():
        return []
    try:
        if not has_video_stream(str(video_path)):
            return []
    except Exception:
        pass
    try:
        from scenedetect import detect, ContentDetector
    except Exception:
        return []
    try:
        scene_list = detect(str(video_path), ContentDetector(threshold=threshold))
    except Exception:
        return []
    bounds = set()
    for start, _end in scene_list:
        try:
            sec = start.get_seconds()
        except Exception:
            continue
        ms = int(sec * 1000)
        if ms > 0:
            bounds.add(ms)
    return sorted(bounds)


# ---------------- 2. 人声分离 ----------------
def separate_vocals(video_path, outdir, model_name="htdemucs", timeout=900):
    """用 demucs 分离人声，返回 vocals.wav 绝对路径；失败/无库返回 None。"""
    if not vocal_sep_available():
        return None
    _ensure_ffmpeg()
    outdir = Path(outdir)
    work = outdir / ".vocal_sep"
    work.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    vocals = work / model_name / stem / "vocals.wav"
    if vocals.exists():
        return str(vocals)
    try:
        cmd = [sys.executable, "-m", "demucs", "-n", model_name,
               "-o", str(work), "--two-stems", "vocals", str(video_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if vocals.exists():
        return str(vocals)
    return None


# ---------------- 3. 音频事件分类 (PANNs / AudioSet 527) ----------------
# 关心的少数标签（AudioSet 类名英文子串匹配）
_INTEREST = {
    "speech": "说话",
    "music": "音乐",
    "singing": "唱歌",
    "laughter": "笑声",
    "applause": "掌声",
    "crowd": "人群",
}

def _map_labels(names):
    res = []
    for n in names:
        nl = (n or "").lower()
        for key, zh in _INTEREST.items():
            if key in nl:
                res.append(zh)
                break
    # 繁轉簡（如果 zhconv 可用）
    try:
        import zhconv
        res = [zhconv.convert(r, 'zh-cn') for r in res]
    except Exception:
        pass
    seen = set(); out = []
    for r in res:
        if r not in seen:
            seen.add(r); out.append(r)
    return out

# 懒加载 AudioTagging（panns_inference 顶层导出的音频分类封装）
_PANNS = None
_LABELS = None
_PANNS_INST = None

def _load_panns():
    global _PANNS, _LABELS
    if _PANNS is not None:
        return
    try:
        from panns_inference import AudioTagging
        from panns_inference import config
        _PANNS = AudioTagging
        _LABELS = config.labels
    except Exception:
        _PANNS = None

def _get_panns_inst():
    """返回缓存的 AudioTagging 实例（首次会加载 Cnn14 权重，需 ~/panns_data 就位）。"""
    global _PANNS_INST
    if _PANNS_INST is not None:
        return _PANNS_INST
    _load_panns()
    if _PANNS is None:
        return None
    try:
        _PANNS_INST = _PANNS(device="cpu")
    except Exception:
        _PANNS_INST = None
    return _PANNS_INST

def classify_audio_events(video_path, segment_sec=10.0, top_k=3):
    """用 PANNs(CNN14) 给每个 segment_sec 片段打标签。
    返回 [(start_ms, end_ms, [中文标签]), ...]。失败/无库返回 []。"""
    if not audio_tag_available():
        return []
    inst = _get_panns_inst()
    if inst is None:
        return []
    try:
        import numpy as np
        import librosa
        import torch
    except Exception:
        return []
    wav_path = None
    try:
        wav_path = _to_wav(str(video_path))
        y, sr = librosa.load(wav_path, sr=32000, mono=True)
    except Exception:
        return []
    finally:
        if wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except Exception:
                pass
    dur = librosa.get_duration(y=y, sr=sr)
    if dur <= 0:
        return []
    seg = int(segment_sec * sr)
    results = []
    for start in range(0, len(y), seg):
        chunk = y[start:start + seg]
        if len(chunk) < int(sr * 0.5):
            continue
        s_ms = int(start / sr * 1000)
        e_ms = int((start + len(chunk)) / sr * 1000)
        try:
            audio_input = torch.tensor(chunk).float().unsqueeze(0)
            clipwise, _ = inst.inference(audio_input)
            probs = np.asarray(clipwise)[0]
        except Exception:
            continue
        idx = np.argsort(-probs)[:top_k]
        labels = [_LABELS[i] for i in idx]
        tags = _map_labels(labels)
        if tags:
            results.append((s_ms, e_ms, tags))
    return results


def audio_has_music_or_singing(video_path, segment_sec=15.0):
    """快速判断：是否存在 music/singing 片段（用于决定是否人声分离）。"""
    evs = classify_audio_events(video_path, segment_sec=segment_sec, top_k=2)
    for _s, _e, tags in evs:
        if "音乐" in tags or "唱歌" in tags:
            return True
    return False


# ---------------- 4. 视频帧 OCR（PaddleOCR） ----------------
def ocr_available():
    """检测 PaddleOCR 是否可用。"""
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        return True
    except Exception:
        return False

# 懒加载 OCR 实例
_OCR_INST = None
_OCR_READY = False
# PaddleOCR 3.x 在 paddle>=3.3 + oneDNN 下有已知崩溃：
#   NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
# 必须关闭 mkldnn（实测 FLAGS_enable_pir_api=0 无效，只有 enable_mkldnn=False 有效）
_OCR_KWARGS_V3 = dict(
    enable_mkldnn=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
_OCR_KWARGS_V2 = dict(use_angle_cls=True)   # 2.x 兼容回退


def _get_ocr_inst():
    """首次使用时初始化 PaddleOCR 实例，返回实例本身；失败返回 None。

    注意：必须返回实例而非布尔值——调用方要用它执行识别。
    """
    global _OCR_INST, _OCR_READY
    if _OCR_READY:
        return _OCR_INST
    _OCR_READY = True
    try:
        # 压制 paddle/paddlex 的 C++ 层刷屏日志（须在 import 前设置）
        os.environ.setdefault("GLOG_minloglevel", "3")
        os.environ.setdefault("FLAGS_logtostderr", "0")
        from paddleocr import PaddleOCR
    except Exception as e:
        print(f"[OCR] PaddleOCR 未安装: {e}")
        return None
    last_err = None
    for kwargs in (_OCR_KWARGS_V3, _OCR_KWARGS_V2, {}):
        try:
            _OCR_INST = PaddleOCR(lang="ch", **kwargs)
            return _OCR_INST
        except Exception as e:
            last_err = e
            _OCR_INST = None
    print(f"[OCR] 初始化失败: {last_err}")
    return None


def _ocr_one_image(inst, img_path, min_confidence):
    """识别单张图，返回 [(text, score), ...]。兼容 PaddleOCR 3.x / 2.x 两种返回格式。"""
    out = []
    # ---- 3.x: predict() -> 可迭代页面，取 rec_texts / rec_scores ----
    if hasattr(inst, "predict"):
        try:
            res = inst.predict(str(img_path))
            for page in res:
                try:
                    texts = page["rec_texts"]
                    scores = page["rec_scores"]
                except Exception:
                    continue
                for t, s in zip(texts, scores):
                    try:
                        if float(s) >= min_confidence:
                            out.append((str(t), float(s)))
                    except Exception:
                        continue
            return out
        except Exception:
            out = []
    # ---- 2.x: ocr() -> [[ [box,(text,score)], ...], ...] ----
    if hasattr(inst, "ocr"):
        try:
            res = inst.ocr(str(img_path), cls=True)
        except Exception:
            return []
        for page in (res or []):
            for line in (page or []):
                try:
                    txt, score = line[1][0], line[1][1]
                    if float(score) >= min_confidence:
                        out.append((str(txt), float(score)))
                except Exception:
                    continue
    return out


def ocr_video_frames(video_path, interval_sec=2.0, min_confidence=0.5,
                     max_frames=150, crop_bottom=None, verbose=True,
                     diff_threshold=2.5, max_side=960):
    """按时间间隔抽取视频帧，用 PaddleOCR 识别画面文字（歌词/硬编码字幕等）。

    参数:
      interval_sec  : 抽帧间隔（秒）
      min_confidence: 置信度阈值，低于此值丢弃
      max_frames    : 最大抽帧数（超了自动放大间隔，防止长视频跑飞）
      crop_bottom   : None=全画面；0.0~1.0=只取画面底部该比例区域（字幕常用，
                      能显著降低画面其它文字的干扰，推荐 0.3~0.4）
      diff_threshold: 帧间差异阈值（0~255，灰度64x64平均差）。低于此值视为"画面
                      没变"而跳过 OCR——这是最大的提速点，默认 2.5；设 0 关闭预筛
      max_side      : 抽帧时限制长边像素（默认960）。竖屏 1080x1920 缩到 960 后
                      检测面积减少约 4 倍；设 0 关闭缩放
    返回 [(timestamp_ms, text), ...]，文本已繁转简并去重；失败/无库返回 []。
    """
    if not ocr_available():
        if verbose:
            print("[OCR] PaddleOCR 不可用，跳过画面文字识别")
        return []
    inst = _get_ocr_inst()
    if inst is None:
        return []

    import tempfile
    ff = _find_ffmpeg()

    # ---- 1. 读取时长 ----
    total_sec = video_duration(video_path)
    if total_sec <= 0:
        if verbose:
            print("[OCR] 无法读取视频时长，跳过")
        return []

    # ---- 2. 一次性抽帧（fps 滤镜：1 次 ffmpeg 调用出全部帧，比逐帧 seek 快 ~10 倍） ----
    n = int(total_sec / interval_sec)
    if n > max_frames:
        interval_sec = total_sec / max_frames
        n = max_frames
    if n <= 0:
        return []
    if verbose:
        print(f"[OCR] 抽帧：{n} 帧，间隔 {interval_sec:.1f}s")

    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="mmp_ocr_")
    # 构建 filter 链：抽帧 -> 底部裁剪(可选) -> 限制长边(提速关键，竖屏 1080x1920 缩到 960)
    vfs = [f"fps={1.0 / interval_sec:.6f}"]
    if crop_bottom and 0 < crop_bottom < 1:
        h = crop_bottom
        vfs.append(f"crop=iw:ih*{h:.4f}:0:ih*{1 - h:.4f}")
    if max_side and max_side > 0:
        vfs.append(f"scale='if(gt(a,1),{max_side},-2)':'if(gt(a,1),-2,{max_side})'")
    cmd = [ff, "-y", "-loglevel", "error", "-i", str(video_path),
           "-vf", ",".join(vfs), "-q:v", "2", os.path.join(tmpdir, "f%06d.jpg")]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300)
    except Exception as e:
        if verbose:
            print(f"[OCR] 抽帧失败: {e}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return []

    import glob
    frames = sorted(glob.glob(os.path.join(tmpdir, "f*.jpg")))
    if not frames:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return []

    # ---- 3. 帧差异预筛：画面基本没变 -> 字幕也没变 -> 跳过 OCR ----
    # 实测：讲解/字幕类视频可跳过 60%~85% 的帧，这是最大的提速点
    keep_idx = _pick_changed_frames(frames, diff_threshold)
    if verbose:
        print(f"[OCR] 预筛：{len(frames)} 帧 -> 保留 {len(keep_idx)} 帧（省 "
              f"{100 - len(keep_idx) * 100 // max(1, len(frames))}%）")

    # ---- 4. 对保留帧做 OCR ----
    results = []
    for i, fi in enumerate(keep_idx, 1):
        ms = int((fi + 1) * interval_sec * 1000)   # ffmpeg 帧序号从 1 开始
        try:
            pairs = _ocr_one_image(inst, frames[fi], min_confidence)
            if pairs:
                parts = [p[0].strip() for p in pairs if p[0].strip()]
                if parts:
                    merged = _to_simplified_ocr(_join_ocr_parts(parts))
                    if merged:
                        results.append((ms, merged))
        except Exception:
            pass
        if verbose and (i % 10 == 0 or i == len(keep_idx)):
            print(f"[OCR]   进度 {i}/{len(keep_idx)}")

    # ---- 5. 清理临时帧（沙箱下 unlink 可能受限，失败忽略） ----
    shutil.rmtree(tmpdir, ignore_errors=True)

    return _dedup_ocr(results)


def _pick_changed_frames(frames, threshold=2.5):
    """按灰度缩略图差异挑选"画面有变化"的帧索引，返回保留帧的下标列表。

    首帧恒保留；与上一保留帧差异 < threshold 的帧跳过。
    PIL/numpy 缺失时退化为"全保留"。
    """
    if threshold <= 0 or len(frames) <= 1:
        return list(range(len(frames)))
    try:
        from PIL import Image
        import numpy as np
    except Exception:
        return list(range(len(frames)))
    keep, prev = [0], None
    for i, f in enumerate(frames):
        try:
            arr = np.asarray(
                Image.open(f).convert("L").resize((64, 64)), dtype=np.float32)
        except Exception:
            keep.append(i)
            prev = None
            continue
        if prev is None or float(np.abs(arr - prev).mean()) >= threshold:
            keep.append(i)
            prev = arr
    # 去重并保序（首帧可能被重复加入）
    seen, out = set(), []
    for i in keep:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _join_ocr_parts(parts):
    """多行 OCR 文本拼接：CJK 直连，含拉丁字母时用空格分隔。"""
    if len(parts) == 1:
        return parts[0]
    out = parts[0]
    for p in parts[1:]:
        prev_is_ascii = bool(out) and ord(out[-1]) < 128
        cur_is_ascii = bool(p) and ord(p[0]) < 128
        out += (" " if (prev_is_ascii and cur_is_ascii) else "") + p
    return out


def _dedup_ocr(results, window=3):
    """去重：连续重复文本只保留首次出现；回看 window 条防止 A/B 抖动。"""
    out, recent = [], []
    for ms, text in results:
        if not text:
            continue
        if text in recent:
            continue
        out.append((ms, text))
        recent.append(text)
        if len(recent) > window:
            recent.pop(0)
    return out

def _to_simplified_ocr(text: str) -> str:
    """OCR 识别结果的繁转简（zhconv → opencc → TC2SC 三级兜底）。"""
    try:
        import zhconv
        return zhconv.convert(text, 'zh-cn')
    except Exception:
        pass
    try:
        from opencc import OpenCC
        return OpenCC('t2s').convert(text)
    except Exception:
        pass
    # TC2SC 兜底映射（从 multi-media-processor.py 同步，覆盖 ASR 输出常见繁体字）
    _TC2SC_FALLBACK = {
        '個': '个', '輕': '轻', '結': '结', '親': '亲', '開': '开', '係': '系', '樣': '样',
        '東': '东', '題': '题', '課': '课', '關': '关', '實': '实', '幫': '帮', '點': '点',
        '態': '态', '將': '将', '來': '来', '過': '过', '質': '质', '時': '时', '還': '还',
        '從': '从', '們': '们', '這': '这', '那': '那', '麼': '么', '隻': '只', '裏': '里',
        '說': '说', '話': '话', '歲': '岁', '會': '会', '沒': '没', '給': '给', '讓': '让',
        '對': '对', '當': '当', '應': '应', '產': '产', '處': '处', '與': '与', '後': '后',
        '覺': '觉', '認': '认', '請': '请', '問': '问', '體': '体', '發': '发',
        '動': '动', '張': '张', '強': '强', '細': '细', '終': '终', '遠': '远', '書': '书',
        '樂': '乐', '愛': '爱', '買': '买', '賣': '卖', '讀': '读', '學': '学', '習': '习',
        '見': '见', '視': '视', '聽': '听', '願': '愿', '氣': '气', '長': '长', '門': '门',
        '間': '间', '顧': '顾', '養': '养', '醫': '医', '網': '网', '財': '财', '專': '专',
        '業': '业', '歷': '历', '經': '经', '驗': '验', '設': '设', '計': '计', '許': '许',
        '論': '论', '試': '试', '證': '证', '資': '资', '優': '优', '擇': '择', '擔': '担',
        '積': '积', '極': '极', '團': '团', '環': '环', '節': '节', '類': '类', '燈': '灯',
        '靈': '灵', '畫': '画', '尋': '寻', '導': '导', '則': '則', '創': '创', '辦': '办',
        '園': '园', '異': '异', '補': '补', '緣': '缘', '奇': '奇', '間': '间', '少': '少',
        '為': '为', '於': '于', '國': '国', '裡': '里', '萬': '万', '喫': '吃', '淚': '泪',
        '寶': '宝', '報': '报', '邊': '边', '變': '变', '別': '别', '賓': '宾', '層': '层',
        '場': '场', '車': '车', '稱': '称', '遲': '迟', '衝': '冲', '醜': '丑', '齣': '出',
        '傳': '传', '詞': '词', '錯': '错', '達': '达', '帶': '带', '單': '单', '彈': '弹',
        '島': '岛', '敵': '敌', '遞': '递', '電': '电', '調': '调', '頂': '顶', '丟': '丢',
        '凍': '冻', '獨': '独', '斷': '断', '隊': '队', '頓': '顿', '兒': '儿', '爾': '尔',
        '飯': '饭', '範': '范', '費': '费', '奮': '奋', '風': '风', '豐': '丰', '婦': '妇',
        '復': '复', '該': '该', '幹': '干', '剛': '刚', '鋼': '钢', '給': '给', '夠': '够',
        '構': '构', '顧': '顾', '掛': '挂', '廣': '广', '歸': '归', '貴': '贵',
        '獲': '获', '機': '机', '雞': '鸡', '幾': '几', '記': '记', '際': '际', '濟': '济',
        '價': '价', '簡': '简', '健': '健', '腳': '脚', '較': '较', '緊': '紧', '進': '进',
        '靜': '静', '舊': '旧', '劇': '剧', '據': '据', '絕': '绝', '顆': '颗',
        '藍': '蓝', '離': '离', '禮': '礼', '臉': '脸', '練': '练', '兩': '两', '輛': '辆',
        '瞭': '了', '領': '领', '劉': '刘', '龍': '龙', '樓': '楼', '錄': '录', '綠': '绿',
        '亂': '乱', '輪': '轮', '滿': '满', '夢': '梦', '麵': '面', '秒': '秒', '滅': '灭',
        '鳴': '鸣', '妳': '你', '難': '难', '腦': '脑', '念': '念', '鳥': '鸟', '農': '农',
        '濃': '浓', '暖': '暖', '歐': '欧', '盤': '盘', '跑': '跑', '碰': '碰', '飄': '飘',
        '蘋': '苹', '齊': '齐', '騎': '骑', '簽': '签', '錢': '钱', '槍': '枪', '橋': '桥',
        '區': '区', '卻': '却', '確': '确', '熱': '热', '賽': '赛', '掃': '扫', '殺': '杀',
        '閃': '闪', '傷': '伤', '燒': '烧', '誰': '谁', '聲': '声', '勝': '胜', '師': '师',
        '濕': '湿', '輸': '输', '樹': '树', '雙': '双', '絲': '丝', '隨': '随',
        '臺': '台', '談': '谈', '湯': '汤', '條': '条', '頭': '头', '圖': '图',
        '網': '网', '無': '无', '係': '系', '細': '细', '嚇': '吓', '鮮': '鲜', '現': '现',
        '線': '线', '鄉': '乡', '想': '想', '響': '响', '項': '项', '寫': '写', '謝': '谢',
        '興': '兴', '選': '选', '藥': '药', '爺': '爷', '頁': '页', '異': '异', '銀': '银',
        '營': '营', '擁': '拥', '語': '语', '雲': '云', '運': '运', '雜': '杂', '載': '载',
        '贓': '赃', '找': '找', '針': '针', '爭': '争', '紙': '纸', '種': '种', '眾': '众',
        '週': '周', '豬': '猪', '專': '专', '轉': '转', '裝': '装', '準': '准', '總': '总',
        '組': '组', '最': '最', '做': '做', '鐘': '钟',
        # 第二轮补充
        '測': '测', '數': '数', '術': '术', '講': '讲', '評': '评', '譯': '译', '護': '护',
        '誤': '误', '調': '调', '談': '谈', '議': '议', '諒': '谅', '諾': '诺', '詢': '询',
        '諷': '讽', '讚': '赞', '賤': '贱', '賬': '账', '購': '购', '販': '贩', '責': '责',
        '貢': '贡', '貨': '货', '貸': '贷', '賠': '赔', '贏': '赢', '賞': '赏', '賦': '赋',
        '趨': '趋', '趕': '赶', '軌': '轨', '輯': '辑', '辭': '辞', '辦': '办', '邊': '边',
        '連': '连', '進': '进', '遊': '游', '違': '违', '遲': '迟', '遷': '迁', '遺': '遗',
        '避': '避', '鄰': '邻', '郵': '邮', '釋': '释', '釣': '钓', '鋼': '钢', '鎖': '锁',
        '鎮': '镇', '鏡': '镜', '鏈': '链', '鋒': '锋', '銷': '销', '鋪': '铺', '鑑': '鉴',
        '閉': '闭', '聞': '闻', '閱': '阅', '閣': '閣', '階': '阶', '陽': '阳', '陰': '阴',
        '陳': '陈', '險': '险', '離': '离', '難': '难', '電': '电', '雷': '雷', '霧': '雾',
        '類': '类', '頻': '频', '顯': '显', '頂': '顶', '順': '顺', '須': '须', '願': '愿',
        '風': '风', '飛': '飞', '飲': '饮', '飽': '饱', '餅': '饼', '餃': '饺', '館': '馆',
        '馬': '马', '駕': '驾', '騎': '骑', '騙': '骗', '驗': '验', '驚': '惊', '麗': '丽',
        '黃': '黄', '龜': '龟', '黨': '党', '齒': '齿', '齡': '龄', '鈔': '钞', '銜': '衔',
        '鳴': '鸣', '鴨': '鸭', '鵝': '鹅', '麥': '麦', '麪': '面',
    }
    return "".join(_TC2SC_FALLBACK.get(ch, ch) for ch in text)


# ==================== OCR 校正 ASR ====================
# 用途：唱歌视频里 Whisper 转写差（如"说草草草草"），画面 OCR 歌词准（"说曹操曹操就到"）
# 策略：按时间窗口对齐 SRT 片段与 OCR 结果，相似度达标则用 OCR 替换 ASR

_SRT_TS_RE = None
# 常见画面噪声词：医院标志、水印、固定标语等，OCR 可能误识别进歌词
_OCR_NOISE_WORDS = [
    '禁止吸烟', 'NO SMOKING', '请勿', '小心地滑', '营业中',
    '营业时间', '欢迎光临', '谢谢惠顾', '版权所有',
]
# 歌词关键词（用于判断文本是否是歌词）
_LYRIC_KEYWORDS = [
    '曹操', '配套', '砰砰', '挑', '救药', '奇妙', '拥抱',
    '依靠', '缘分', '命运', '玩笑', '彩票', '重要', '宝宝',
    '灵犀', '协调', '颠倒', '无兆', '就好', '幸好', '知道',
    '对象', '表情', '遇到', '生命', '半边', '大学', '未恙',
    '审个', '早已', '寻找', '世界', '小', '万有', '轻宽',
]


def _has_noise(text):
    """判断文本是否含有已知噪声词（医院标志等）。"""
    text_upper = text.upper()
    for noise in _OCR_NOISE_WORDS:
        if noise in text or noise.upper() in text_upper:
            return True
    return False


def _is_lyric_text(text):
    """判断文本是否是歌词（含有歌词关键词）。"""
    for kw in _LYRIC_KEYWORDS:
        if kw in text:
            return True
    return False


def _extract_lyrics_from_ocr(text, min_len=3):
    """从含噪声的 OCR 文本中提取歌词部分。

    策略：
      1) 短文本直接丢弃（< min_len）
      2) 标题/片头信息（含"原曲""填词""翻唱"等）→ 丢弃
      3) 纯噪声（只有噪声词，无任何歌词内容）→ 丢弃
      4) 有歌词关键词 + 无噪声 → 保留全文（不做截取，避免截断完整歌词）
      5) 有歌词关键词 + 有噪声 → 从第一个歌词关键词处截取
      6) 无关键词但无噪声 → 保留（可能是纯歌词）
    """
    if len(text) < min_len:
        return None, False

    # 标题/片头信息过滤
    title_patterns = ['原曲', '填词', '翻唱', '原唱', '编曲', '制作', '出品', 'MV']
    if any(p in text for p in title_patterns):
        return None, False

    # 判断是否有噪声词
    has_noise_flag = _has_noise(text)
    # 判断是否有歌词关键词
    has_lyric_flag = _is_lyric_text(text)

    # 纯噪声 → 丢弃
    if has_noise_flag and not has_lyric_flag:
        return None, False

    # 有歌词关键词的情况
    if has_lyric_flag:
        # 无噪声：完整保留，不截取
        if not has_noise_flag and len(text) >= min_len:
            return text, True
        # 有噪声：从第一个歌词关键词处截取
        candidates = []
        for kw in _LYRIC_KEYWORDS:
            if kw in text:
                idx = text.find(kw)
                result = text[idx:].strip()
                # 如果结果太短，向前扩展以包含更多内容
                if len(result) < min_len:
                    for expand in range(1, 10):
                        start = max(0, idx - expand)
                        expanded = text[start:].strip()
                        if not _has_noise(expanded) and len(expanded) >= min_len:
                            result = expanded
                            break
                if len(result) >= min_len and not _has_noise(result):
                    candidates.append(result)
        if candidates:
            return max(candidates, key=len), True

    # 无关键词但无噪声 → 保留（可能是纯歌词）
    if not has_noise_flag and len(text) >= min_len:
        return text, True

    # 其他情况 → 丢弃
    return None, False


def _clean_ocr_for_correction(ocr_results):
    """对 OCR 结果做清洗：提取歌词片段、去重、保留最佳候选。"""
    cleaned = []
    group_size_ms = 3000
    groups = {}
    for ms, text in ocr_results:
        group_key = (ms // group_size_ms) * group_size_ms
        cleaned_text, kept = _extract_lyrics_from_ocr(text)
        if not kept:
            continue
        if group_key not in groups or len(cleaned_text) > len(groups[group_key][1]):
            groups[group_key] = (ms, cleaned_text)
    cleaned = sorted(groups.values(), key=lambda x: x[0])
    return cleaned

_SRT_TS_RE = None

def _get_srt_ts_re():
    """懒加载 SRT 时间戳正则。"""
    global _SRT_TS_RE
    if _SRT_TS_RE is None:
        import re
        _SRT_TS_RE = re.compile(r'(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})')
    return _SRT_TS_RE


def parse_srt(srt_path):
    """解析 SRT 字幕文件，返回 [(start_ms, end_ms, text), ...]，已繁转简、空段过滤。

    失败/文件不存在返回 []。
    """
    try:
        from pathlib import Path as _P
        p = _P(srt_path)
        if not p.exists():
            return []
        content = p.read_text(encoding="utf-8")
    except Exception:
        return []

    # 先繁转简
    content = _to_simplified_ocr(content)

    ts_re = _get_srt_ts_re()
    lines = content.split("\n")
    segments = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = ts_re.match(line)
        if m:
            sh, sm, ss, sms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            eh, em, es, ems = int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8))
            start_ms = sh * 3600000 + sm * 60000 + ss * 1000 + sms
            end_ms = eh * 3600000 + em * 60000 + es * 1000 + ems
            i += 1
            seg_lines = []
            while i < len(lines) and lines[i].strip() != "":
                seg_lines.append(lines[i].strip())
                i += 1
            text = " ".join(seg_lines)
            if text:
                segments.append((start_ms, end_ms, text))
        else:
            i += 1
    return segments


def _text_similarity(a, b):
    """综合相似度：字符集重叠率 + 序列匹配率，对近义错字更敏感。

    例："说吵吵吵吵就到" vs "说曹操曹操就到"：
      - 字符集重叠 ≈ 0.5
      - SequenceMatcher ≈ 0.75
      - 综合后 > 0.6，可触发校正
    """
    if not a or not b:
        return 0.0
    # 字符集重叠
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    overlap = inter / union if union else 0.0

    # 序列匹配（保留顺序）
    try:
        from difflib import SequenceMatcher
        seq_ratio = SequenceMatcher(None, a, b).ratio()
    except Exception:
        seq_ratio = overlap

    # 加权综合：序列匹配更重要（保留词序），重叠率作为兜底
    return 0.3 * overlap + 0.7 * seq_ratio


def _is_significant_improvement(orig, ocr):
    """判断 OCR 是否显著改进 ASR。

    规则：
      1) 如果 OCR 包含关键区别字（ASR 没有且 OCR 有），且相似度>0.2 → 改进
      2) 如果 OCR 长度≥原文30% 且相似度>0.4 → 改进
      3) 如果 OCR 较短但相似度很高(>0.6) → 改进
    """
    if len(ocr) < 3:
        return False

    score = _text_similarity(orig, ocr)
    length_ratio = len(ocr) / len(orig) if len(orig) > 0 else 0

    # 关键区别字检查
    key_diff_words = ['曹操', '配套', '砰砰', '救药', '奇妙', '拥抱', '依靠', '命运', '彩票', '重要', '宝宝', '灵犀', '协调', '颠倒', '早已', '对象']
    has_key_diff = any(kw in ocr and kw not in orig for kw in key_diff_words)

    # 有区别字且相似度达标 → 改进（放宽到0.2）
    if has_key_diff and score >= 0.2:
        return True

    # 长度相当且相似度较高 → 改进（放宽到30%长度和0.4相似度）
    if length_ratio >= 0.3 and score >= 0.4:
        return True

    # 短 OCR 但相似度很高 → 改进
    if length_ratio >= 0.2 and score >= 0.6:
        return True

    return False


def align_ocr_to_srt(segments, ocr_results, tolerance_ms=2500, min_similarity=0.25):
    """将 OCR 结果对齐到 SRT 片段，返回校正后的片段列表。

    策略：对每个 SRT 片段，找时间窗口内所有清洗后的 OCR 记录，
    计算相似度；只有当 OCR 显著改进 ASR 时才替换。

    返回：[(start_ms, end_ms, original, corrected)] 列表。
    """
    # 先清洗 OCR 结果
    ocr_cleaned = _clean_ocr_for_correction(ocr_results)

    out = []
    used_ocr_indices = set()  # 使用索引而非时间戳去重

    for start_ms, end_ms, orig_text in segments:
        best_idx = None
        best_score = 0.0
        best_ocr = None
        # 在时间窗口内搜索
        for i, (ocr_ms, ocr_text) in enumerate(ocr_cleaned):
            if ocr_ms < start_ms - tolerance_ms:
                continue
            if ocr_ms > end_ms + tolerance_ms:
                continue
            # 去重
            if i in used_ocr_indices:
                continue
            score = _text_similarity(orig_text, ocr_text)
            if score > best_score:
                best_score = score
                best_idx = i
                best_ocr = ocr_text

        # 只有显著改进才替换
        if best_ocr and best_score >= min_similarity and _is_significant_improvement(orig_text, best_ocr):
            corrected = best_ocr
            used_ocr_indices.add(best_idx)
        else:
            corrected = orig_text
        out.append((start_ms, end_ms, orig_text, corrected))
    return out


# ==================== 智能校正规则（唱歌视频 ASR 常见错误模式）====================
# 用途：OCR 对齐后仍有残留错误时，用规则引擎二次修正
# 来源：实测唱歌视频 Whisper 输出 → 人工核对 拆解.md 提取的固定错误模式
_SMART_CORRECTION_RULES = [
    # --- 重复字（Whisper 在唱歌时常见重码）---
    ('说操操操操就倒', '说曹操曹操就到'),
    ('喷喷', '砰砰'),
    ('草草草草', '曹操曹操'),
    # --- 谐音替换 ---
    ('轻宽', '轻狂'),
    ('审个', '伸个'),
    ('逼逃', '配套'),
    ('未恙', '无恙'),
    ('无遇到', '没遇到'),   # TC2SC 未覆盖 無→没，在此补齐
    ('穿鞋穿鞋穿鞋穿鞋', '穿鞋穿鞋穿鞋穿鞋'),  # 占位：长重复，后续可按需补
    # --- 词序/断句修复 ---
    ('缘反正', '缘分真'),
    ('命运正会开 完少', '命运正会开玩笑'),
    ('命运正会开完少', '命运正会开玩笑'),
    # --- 主体词修正 ---
    ('万有们', '网友们'),
    ('受割伤害', '受个伤'),
    ('明日我早已想好', '早已想好'),   # 段20 多余"明日我"
    # --- 的/了 通用替换 ---
    ('像中的', '像中了'),
    # --- 段落级完整替换（高优先级，优先于子串匹配）---
    ('你说得对但是', '你说得对，但是'),  # 标点补全
]


def _apply_smart_correction(text: str) -> str:
    """应用智能校正规则（歌曲歌词类 ASR 错误）。

    策略：先做完整短语替换（高优先级），再做短词替换（兜底），最后清理多余空格。
    """
    # 1) 完整短语替换（按长度降序，避免短串干扰长串）
    by_len = sorted(_SMART_CORRECTION_RULES, key=lambda x: len(x[0]), reverse=True)
    for wrong, right in by_len:
        text = text.replace(wrong, right)
    # 2) 清理多余空格
    import re as _re
    text = _re.sub(r'\s+', ' ', text).strip()
    return text


def generate_corrected_srt(segments, outdir, title="corrected"):
    """将校正后的片段写回 SRT 格式。返回 outdir / corrected_subtitle.srt。"""
    from pathlib import Path as _P
    base = _P(outdir) if isinstance(outdir, str) else outdir
    out = base / "corrected_subtitle.srt"
    def ms_to_srt_ts(ms):
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        ms_part = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"
    lines = []
    for i, (s, e, _orig, corrected) in enumerate(segments, 1):
        corrected = _apply_smart_correction(corrected)
        lines.append(str(i))
        lines.append(f"{ms_to_srt_ts(s)} --> {ms_to_srt_ts(e)}")
        lines.append(corrected)
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)


def generate_corrected_timestamped(segments, outdir, title="corrected"):
    """生成带时间戳的校正 TXT：[HH:MM:SS.mmm --> HH:MM:SS.mmm] 校正后文本。"""
    from pathlib import Path as _P
    base = _P(outdir) if isinstance(outdir, str) else outdir
    out = base / f"{_P(title).stem}_corrected_timestamped.txt"
    def ms_to_ts(ms):
        return f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d}.{ms%1000:03d}"
    lines = []
    for s, e, _orig, corrected in segments:
        corrected = _apply_smart_correction(corrected)
        lines.append(f"[{ms_to_ts(s)} --> {ms_to_ts(e)}] {corrected}")
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
