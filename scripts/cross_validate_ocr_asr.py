#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_validate_ocr_asr.py — OCR × ASR 跨验证工作流（唱歌/歌词视频精准校正）

对唱歌/翻唱视频，Whisper 转写质量天然吃亏（音高、拖腔与语音模型分布差异大），
而画面硬编码歌词是确定文本。本脚本用**密集采样 + 严格段内匹配**逐句从画面 OCR
中取出歌词，与 ASR 转写交叉验证，精准率可达 95%+。

核心策略（源自 v1.3.0 实测迭代）：
  1. 密集采样：默认 0.1s 间隔抽帧——实测 1s/0.5s/0.3s 间隔会漏掉短句歌词
  2. 严格段内匹配：start_ms <= ts_ms < end_ms（左闭右开），避免歌词跨段混入
  3. 噪声过滤：医院招牌（禁止吸烟/主任/医师等）、OCR 前缀噪声
  4. 质量分级：high（命中 OCR 画面文字）/ low（无 OCR，回退 ASR 规则校正）

用法:
    python scripts/cross_validate_ocr_asr.py <视频路径> <SRT路径> --ocr-interval 0.1
    python scripts/cross_validate_ocr_asr.py <视频> <SRT> --range 43-50 --ocr-interval 0.1
    python scripts/cross_validate_ocr_asr.py <视频> <SRT> --format all --ocr-bottom 0.35

输出产物（默认写入 SRT 同级的 <stem>_crossval/ 目录）:
    cross_validation_final.json     完整验证数据（段号/时间/ASR原音/最终歌词/OCR匹配/质量）
    cross_validation_report.html    可视化对比报告
    <标题>_v13.md                   Markdown 歌词（带时间戳 + ASR 原音 + OCR 画面对照）
    <标题>_v13.txt                  纯歌词
    <标题>_v13.srt                  SRT 字幕

依赖：paddleocr / paddlepaddle（CPU 版）、ffmpeg（技能自带优先）。
首次使用会下载 OCR 模型（约 200MB），之后缓存复用。
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

# 复用 scene_audio 的既有能力，避免重复实现（含 PaddleOCR 3.x 兼容与 oneDNN 规避）
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from scene_audio import (
        _find_ffmpeg, _get_ocr_inst, _ocr_one_image, _to_simplified_ocr,
        parse_srt, _apply_smart_correction, video_duration,
    )
except Exception as e:  # pragma: no cover - 环境缺依赖时给出明确提示
    print(f"[错误] 无法导入 scene_audio 模块（应与本脚本同目录）: {e}")
    sys.exit(2)

# ---------------- 噪声词表（医院招牌等固定干扰，实测样本来自医院背景翻唱视频） ----------------
NOISE_KEYWORDS = [
    '禁止', '吸烟', 'NO SMOK', '主任', '医师', '医务', 'BP',
    '出国师', '中西', '新结', '医码', '医销', '医国',
]
NOISE_PREFIXES = [
    '禁止吸烟', '禁止吸煙', '医销', '医码', '出医码', '出服', '出银物', '出银销',
]


def log(msg):
    print(msg, flush=True)


# ---------------- 抽帧 ----------------
def extract_frames_oneshot(ffmpeg, video, start_s, end_s, interval, tmpdir, bottom_ratio=None):
    """一次性抽帧（fps 滤镜），比逐帧 seek 快一个数量级。

    返回 [(timestamp_sec, frame_path), ...]；时间戳按 start + i*interval 推导，
    误差不超过一个采样间隔（对段内匹配足够）。
    """
    span = max(end_s - start_s, interval)
    fps = 1.0 / interval
    vf = f"fps={fps:.6f}"
    if bottom_ratio:
        r = float(bottom_ratio)
        vf = f"crop=iw:ih*{r:.4f}:0:ih*{1 - r:.4f}," + vf
    out_pattern = str(tmpdir / "f_%05d.jpg")
    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', f"{start_s:.3f}", '-t', f"{span:.3f}", '-i', str(video),
        '-vf', vf, '-q:v', '2', out_pattern,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:
        log(f"[抽帧] 一次性抽帧失败: {e}")
        return []

    frames = []
    for idx, p in enumerate(sorted(tmpdir.glob("f_*.jpg"))):
        frames.append((start_s + idx * interval, p))
    return frames


def extract_frame_seek(ffmpeg, video, ts, out_path, timeout=20):
    """逐帧 seek 抽帧（备用路径，时间戳精确）。"""
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
           '-ss', f"{ts:.3f}", '-i', str(video), '-frames:v', '1', '-q:v', '2', str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0 and out_path.exists()
    except Exception:
        return False


# ---------------- OCR ----------------
def clean_ocr_text(text, min_len=2):
    """清洗单条 OCR 文本：繁转简 → 噪声词/前缀过滤 → 长度门控。返回 None 表示丢弃。"""
    text = (text or '').strip()
    if not text:
        return None
    text = _to_simplified_ocr(text)
    for noise in NOISE_KEYWORDS:
        if noise in text and len(text) < len(noise) + 15:
            return None
    for prefix in NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) < min_len:
        return None
    if _is_junk(text):
        return None
    return text


def ocr_frames(ocr, frames, min_score=0.3):
    """对帧序列逐帧 OCR，返回 [(ts_ms, text), ...]（已过滤与清洗）。"""
    results = []
    total = len(frames)
    for i, (ts, path) in enumerate(frames, 1):
        if total > 20 and (i == 1 or i % 20 == 0 or i == total):
            log(f"    OCR 进度 {i}/{total} 帧")
        try:
            for text, score in _ocr_one_image(ocr, path, min_score):
                if score is not None and score < min_score:
                    continue
                cleaned = clean_ocr_text(text)
                if cleaned:
                    results.append((int(round(ts * 1000)), cleaned))
        except Exception as e:
            log(f"    OCR 错误 @{ts:.1f}s: {e}")
    return results


def _is_junk(text, ascii_min_len=10):
    """判断 OCR 碎片是否为乱码。

    歌词正文至少 2 个汉字；中文字数不足 2 的多是 OCR 抖动碎片（sO / B人 / Ithago）。
    例外：完全不含中文但足够长的串（真实英文歌词）保留。
    """
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    if cjk >= 2:
        return False
    if cjk == 0:
        return len(text) < ascii_min_len
    return len(text) < 4


def merge_segment_texts(texts):
    """把同一段内命中的多条 OCR 文本合并成完整歌词。

    歌词常跨行显示（"男才女貌" / "地设天造"），只取单条最长会丢内容。
    策略：
      1) 剔除乱码与被更长文本包含的碎片（"男才女"⊂"男才女貌"）
      2) 帧持续性过滤：画面歌词会持续显示多帧，只出现一帧的多为 OCR 抖动或招牌碎片
      3) 按首次出现顺序拼接
    """
    cand = [t for t in texts if t and not _is_junk(t)]
    if not cand:
        return ""
    order, count = {}, {}
    for i, t in enumerate(cand):
        order.setdefault(t, i)
        count[t] = count.get(t, 0) + 1

    uniq = []
    for t in sorted(set(cand), key=lambda x: -len(x)):
        if any(t in u for u in uniq):
            continue
        uniq.append(t)

    # 帧持续性过滤：有稳定文本时，丢掉只闪一帧的碎片
    stable = [t for t in uniq if count[t] >= 2]
    if stable:
        uniq = stable
    uniq.sort(key=lambda t: order.get(t, 0))
    return " ".join(uniq)


# ---------------- 严格段内匹配 ----------------
def match_ocr_in_segment(ocr_lyrics, start_ms, end_ms, min_len=2):
    """严格段内匹配：left-closed / right-open，避免歌词跨段混入。

    同一句歌词会被多帧重复识别 → 去重后合并为完整歌词（见 merge_segment_texts）。
    """
    hits = [(ts, t) for ts, t in ocr_lyrics if start_ms <= ts < end_ms and len(t) >= min_len]
    if not hits:
        return None, []
    texts = []
    for _, t in hits:
        if t not in texts:
            texts.append(t)
    # 注意：合并时传原始 hits（保留重复），帧持续性统计依赖重复次数
    merged = merge_segment_texts([t for _, t in hits])
    return (merged or None), texts


def build_srt_bar(ts_ms):
    h, rem = divmod(int(ts_ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_ts(ts_ms):
    m, rem = divmod(int(ts_ms), 60000)
    s, _ = divmod(rem, 1000)
    return f"{m:02d}:{s:02d}"


# ---------------- 主流程 ----------------
def parse_range(spec):
    """解析 --range 43-50 → (43.0, 50.0)；非法返回 None。"""
    if not spec:
        return None
    m = re.match(r'^\s*([\d.]+)\s*[-~]\s*([\d.]+)\s*$', spec)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def main():
    ap = argparse.ArgumentParser(
        description="OCR × ASR 跨验证工作流（唱歌/歌词视频精准校正）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("video", help="视频文件路径")
    ap.add_argument("srt", help="ASR 字幕（SRT）路径")
    ap.add_argument("--range", dest="range_spec", default=None,
                    help="只验证指定时间范围，格式 43-50（秒）")
    ap.add_argument("--ocr-interval", type=float, default=0.1,
                    help="抽帧间隔（秒），默认 0.1（密集采样；1s/0.5s 会漏歌词）")
    ap.add_argument("--ocr-bottom", type=float, default=None,
                    help="只识别画面底部该比例区域（如 0.35），可滤掉画面其它文字并提速")
    ap.add_argument("--min-score", type=float, default=0.3, help="OCR 置信度阈值，默认 0.3")
    ap.add_argument("--format", default="all",
                    choices=["json", "html", "md", "txt", "srt", "all"],
                    help="输出格式，默认 all")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 SRT 同级 <stem>_crossval/）")
    ap.add_argument("--title", default=None, help="标题（默认取视频文件名主干）")
    ap.add_argument("--keep-frames", action="store_true", help="保留抽帧图片（默认清理）")
    args = ap.parse_args()

    # Windows 控制台默认 GBK，中文日志会乱码 → 强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    video = Path(args.video)
    srt = Path(args.srt)
    if not video.exists():
        log(f"[错误] 视频不存在: {video}")
        return 2
    if not srt.exists():
        log(f"[错误] 字幕不存在: {srt}")
        return 2

    title = args.title or video.stem
    outdir = Path(args.out_dir) if args.out_dir else srt.parent / f"{srt.stem}_crossval"
    outdir.mkdir(parents=True, exist_ok=True)

    interval = max(args.ocr_interval, 0.05)
    rng = parse_range(args.range_spec)

    log("=" * 64)
    log("OCR × ASR 跨验证")
    log("=" * 64)

    # 1) 载入 SRT 与 OCR 引擎
    segments = parse_srt(str(srt))
    if not segments:
        log(f"[错误] SRT 解析为空: {srt}")
        return 2
    dur = video_duration(str(video))
    log(f"[1/5] SRT 段落 {len(segments)} 条 | 视频时长 {dur:.1f}s | 抽帧间隔 {interval}s")
    log(f"      匹配范围: {'全片' if not rng else f'{rng[0]:.1f}s - {rng[1]:.1f}s'}")

    ocr = _get_ocr_inst()
    if not ocr:
        log("[错误] PaddleOCR 不可用。请先安装: pip install paddleocr paddlepaddle")
        return 2

    # 2) 确定采样窗口并抽帧
    if rng:
        win_start, win_end = rng
    else:
        win_start = max(segments[0][0] / 1000.0 - interval, 0.0)
        win_end = segments[-1][1] / 1000.0 + interval
    if dur:
        win_end = min(win_end, dur)

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        log("[错误] 未找到 ffmpeg")
        return 2

    tmpdir = Path(tempfile.mkdtemp(prefix="crossval_frames_"))
    try:
        log(f"[2/5] 密采抽帧 {win_start:.2f}s → {win_end:.2f}s @ {interval}s ...")
        frames = extract_frames_oneshot(ffmpeg, video, win_start, win_end, interval,
                                        tmpdir, args.ocr_bottom)
        if len(frames) <= 1:
            log("      一次性抽帧结果偏少，回退逐帧 seek ...")
            for p in tmpdir.glob("f_*.jpg"):
                p.unlink()
            frames = []
            t = win_start
            i = 0
            while t <= win_end:
                fp = tmpdir / f"f_{i:05d}.jpg"
                if extract_frame_seek(ffmpeg, video, t, fp):
                    frames.append((t, fp))
                i += 1
                t = win_start + i * interval
        log(f"      实际抽帧 {len(frames)} 帧")

        # 3) OCR
        log(f"[3/5] 逐帧 OCR（置信度 ≥ {args.min_score}）...")
        ocr_lyrics = ocr_frames(ocr, frames, args.min_score)
        log(f"      有效 OCR 文本 {len(ocr_lyrics)} 条")

        # 4) 严格段内匹配 + 质量分级
        log("[4/5] 严格段内匹配 ...")
        results = []
        target_segs = [
            (s, e, t) for (s, e, t) in segments
            if (not rng) or (e / 1000.0 >= rng[0] and s / 1000.0 <= rng[1])
        ]
        for idx, (start_ms, end_ms, asr_text) in enumerate(target_segs, 1):
            best, hits = match_ocr_in_segment(ocr_lyrics, start_ms, end_ms)
            if best:
                final_text, quality, source = best, "high", "OCR"
            else:
                final_text, quality, source = _apply_smart_correction(asr_text), "low", "ASR"
            results.append({
                "segment": idx,
                "range_ms": [start_ms, end_ms],
                "time": f"{fmt_ts(start_ms)} - {fmt_ts(end_ms)}",
                "asr_raw": asr_text,
                "ocr_matched": hits,
                "final_text": final_text,
                "source": source,
                "quality": quality,
            })

        high = sum(1 for r in results if r["quality"] == "high")
        total = len(results) or 1
        log(f"      段落 {len(results)} | OCR 命中 {high} | 覆盖率 {high / total * 100:.0f}%")
        for r in results:
            mark = "✅" if r["quality"] == "high" else "⚠️"
            log(f"      {mark} 段{r['segment']:02d} [{r['time']}] {r['final_text']}")

        # 5) 输出
        log("[5/5] 生成产物 ...")
        want = args.format

        def wants(kind):
            return want in ("all", kind)

        if wants("json"):
            p = outdir / "cross_validation_final.json"
            p.write_text(json.dumps({
                "video": str(video), "srt": str(srt), "title": title,
                "ocr_interval": interval, "range": args.range_spec,
                "ocr_lines": len(ocr_lyrics), "coverage": f"{high / total * 100:.0f}%",
                "segments": results,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"      JSON: {p}")

        if wants("html"):
            p = outdir / "cross_validation_report.html"
            p.write_text(build_html(title, results, interval, len(ocr_lyrics)), encoding="utf-8")
            log(f"      HTML: {p}")

        if wants("md"):
            p = outdir / f"{title}_v13.md"
            p.write_text(build_md(title, results), encoding="utf-8")
            log(f"      MD: {p}")

        if wants("txt"):
            p = outdir / f"{title}_v13.txt"
            p.write_text("\n".join(r["final_text"] for r in results) + "\n", encoding="utf-8")
            log(f"      TXT: {p}")

        if wants("srt"):
            p = outdir / f"{title}_v13.srt"
            p.write_text(build_srt(results), encoding="utf-8")
            log(f"      SRT: {p}")

        log("=" * 64)
        log(f"完成：OCR 覆盖率 {high}/{len(results)}，产物目录 {outdir}")
        log("=" * 64)
        return 0
    finally:
        if not args.keep_frames:
            shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            log(f"      抽帧保留于: {tmpdir}")


# ---------------- 报告生成 ----------------
def build_md(title, results):
    lines = [f"# {title}", "", f"> OCR × ASR 跨验证（0.1s 密集采样 + 严格段内匹配）", ""]
    for r in results:
        tag = "🟢 OCR" if r["quality"] == "high" else "🟡 ASR"
        lines.append(f"## [{r['time']}] {r['final_text']}")
        lines.append("")
        lines.append(f"- 质量：{r['quality']}（来源 {tag}）")
        if r["asr_raw"] and r["asr_raw"] != r["final_text"]:
            lines.append(f"- ASR 原音：{r['asr_raw']}")
        if r["ocr_matched"]:
            lines.append(f"- OCR 画面：{' / '.join(r['ocr_matched'])}")
        lines.append("")
    return "\n".join(lines)


def build_srt(results):
    blocks = []
    for i, r in enumerate(results, 1):
        s, e = r["range_ms"]
        blocks.append(f"{i}\n{build_srt_bar(s)} --> {build_srt_bar(e)}\n{r['final_text']}\n")
    return "\n".join(blocks)


def build_html(title, results, interval, ocr_lines):
    high = sum(1 for r in results if r["quality"] == "high")
    total = len(results) or 1
    rows = []
    for r in results:
        changed = r["asr_raw"] != r["final_text"]
        badge = ("<span class='b high'>OCR</span>" if r["quality"] == "high"
                 else "<span class='b low'>ASR</span>")
        rows.append(f"""
    <div class="seg">
      <div class="head"><span class="num">{r['segment']}</span>
        <span class="time">{r['time']}</span>{badge}
        {'<span class="b chg">已校正</span>' if changed else ''}</div>
      <div class="grid">
        <div class="panel"><div class="pt">Whisper ASR</div>
          <div class="txt {'err' if changed else ''}">{r['asr_raw'] or '—'}</div></div>
        <div class="panel"><div class="pt">OCR 画面文字</div>
          <div class="txt">{' / '.join(r['ocr_matched']) if r['ocr_matched'] else '无'}</div></div>
        <div class="panel"><div class="pt">最终歌词</div>
          <div class="txt ok">{r['final_text']}</div></div>
      </div>
    </div>""")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OCR × ASR 跨验证 — {title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","Noto Sans SC",sans-serif;background:#f4f6fb;color:#1f2430;padding:24px}}
.wrap{{max-width:1280px;margin:0 auto}}
h1{{font-size:24px;margin-bottom:6px}}
.sub{{color:#697086;font-size:13px;margin-bottom:20px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}}
.card{{background:#fff;border:1px solid #e6e9f2;border-radius:10px;padding:16px;text-align:center}}
.v{{font-size:28px;font-weight:700;color:#3a5bd9}}
.l{{font-size:12px;color:#697086;margin-top:4px}}
.seg{{background:#fff;border:1px solid #e6e9f2;border-radius:10px;padding:16px;margin-bottom:12px}}
.head{{display:flex;align-items:center;gap:10px;margin-bottom:12px}}
.num{{background:#3a5bd9;color:#fff;width:28px;height:28px;border-radius:50%;
display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700}}
.time{{color:#697086;font-size:13px}}
.b{{font-size:11px;padding:3px 9px;border-radius:20px;font-weight:700}}
.high{{background:#e7f6ec;color:#1f8a4c}} .low{{background:#fff4e0;color:#b57200}}
.chg{{background:#e8eeff;color:#3a5bd9}}
.grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}
.panel{{background:#fafbff;border:1px solid #eef1f8;border-radius:8px;padding:12px}}
.pt{{font-size:11px;color:#8b93a7;letter-spacing:1px;margin-bottom:8px;font-weight:700}}
.txt{{font-size:15px;line-height:1.7}}
.err{{color:#c0392b;text-decoration:line-through}}
.ok{{color:#1f8a4c;font-weight:600}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.stats{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">
<h1>OCR × ASR 跨验证报告</h1>
<div class="sub">{title} · 抽帧间隔 {interval}s · OCR 有效文本 {ocr_lines} 条</div>
<div class="stats">
  <div class="card"><div class="v">{total}</div><div class="l">验证段落</div></div>
  <div class="card"><div class="v">{high}</div><div class="l">OCR 命中</div></div>
  <div class="card"><div class="v">{high / total * 100:.0f}%</div><div class="l">覆盖率</div></div>
  <div class="card"><div class="v">{sum(1 for r in results if r['asr_raw'] != r['final_text'])}</div><div class="l">校正处数</div></div>
</div>
{''.join(rows)}
</div></body></html>"""


if __name__ == '__main__':
    sys.exit(main())
