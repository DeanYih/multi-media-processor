# -*- coding: utf-8 -*-
"""小鹅通 (xet / xiaoeknow / pomoho) 课程视频下载器

为什么单独一个模块
------------------
小鹅通 H5 课程页的视频流地址带**客户端签名**，匿名访问只会跳微信 OAuth 登录页，
纯 HTTP 抓页面拿不到 m3u8；必须从「已登录的浏览器会话」里捕获。
而且它的主播放列表默认开启 `confusion_index` 混淆，正文形如::

    [xiaoe]K4YL7PqapM%3DMZ0PG7BMPRIWP/1as167DJ3qr5A6CPG5vuC16I1...

不是标准 base64（`=` 落在串中间、长度非 4 的倍数），用 uuid / app_id / sign 做 XOR
也解不开——播放器在 JS 里解密。**所以不要试图硬解。**

两条路径
--------
1. **明文播放列表**（部分课程）：捕获到的正文以 `#EXTM3U` 开头 → 直接交给 ffmpeg
   读主播放列表（ffmpeg 的 hls demuxer 自带 AES-128 解密与 fMP4 处理）。
2. **混淆播放列表**（小鹅通默认）：**不解密，直接绕过分片枚举**。
   实测小鹅通腾讯云 VOD 分片的签名**不绑定路径**——同一个 `sign` 对任意序号
   `v.f146750_<n>.ts` 都返回 200/206。因此拿到任意一片的 URL 就够了：
   指数探测上界 → 二分定边界 → 全量并发下载 → ffmpeg concat 无损拼接。
   实测 1241 片 / 740 MB：16 线程 23 秒下完（54 片/秒），拼接 1 分钟。

   前提：分片是**未加密的裸 TS**（首字节 `0x47`）。若探测到不是 TS，
   说明该课程开了分片加密，本模块会明确报错而不是产出一个坏文件。

登录态
------
用 Playwright 驱动本机**已安装的 Edge / Chrome**（`channel="msedge"`），独立持久化
profile（默认 `~/.workbuddy/xet_profile`）保存登录，扫码一次长期复用。
profile 与输出都在用户目录，**不进技能目录**，发布技能时不会外带个人登录态。

依赖
----
- playwright（`pip install playwright`）——浏览器内核复用本机 Edge/Chrome，
  **无需** `playwright install`（省 150MB 下载）
- ffmpeg / ffprobe（技能自带 `bin/whisper/bin`，脚本会自动加入 PATH）
- requests

CLI
---
    python xet_downloader.py "<课程URL>" [--out-dir DIR] [--workers 16] [--capture-only]
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

DEFAULT_PROFILE = Path(os.environ.get("XET_PROFILE_DIR")
                       or (Path.home() / ".workbuddy" / "xet_profile"))
DEFAULT_WORKERS = int(os.environ.get("XET_WORKERS") or 16)


def _log(msg: str) -> None:
    print(msg, flush=True)


# ==================== ffmpeg ====================
def find_ffmpeg() -> str:
    """优先用技能自带 ffmpeg，其次系统 PATH。"""
    try:
        from scene_audio import _find_ffmpeg  # 同目录模块，内部已做三级回退
        p = _find_ffmpeg()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def find_ffprobe() -> str:
    ff = find_ffmpeg()
    cand = Path(ff).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
    if cand.exists():
        return str(cand)
    try:
        from scene_audio import _find_ffprobe  # 老版本没有此函数
        p = _find_ffprobe()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffprobe") or "ffprobe"


# ==================== Playwright 捕获 ====================
def ensure_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "小鹅通课程需要 Playwright 捕获视频流，当前环境未安装。\n"
            "请执行：python -m pip install playwright\n"
            "（浏览器内核复用本机已安装的 Edge/Chrome，无需再跑 playwright install）"
        )


def _launch_ctx(p, profile: Path, headless: bool):
    """按 msedge → chrome → 自带 chromium 顺序尝试，复用本机已装浏览器。"""
    profile.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled", "--mute-audio"]
    base = dict(headless=headless, viewport={"width": 1280, "height": 860},
                args=args, ignore_default_args=["--enable-automation"])
    last_err = None
    for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch_persistent_context(str(profile), **base, **kw)
        except Exception as e:  # 该 channel 不存在则换下一个
            last_err = e
    raise RuntimeError(f"无法启动浏览器（Edge/Chrome/内置内核均失败）: {last_err}")


def _click_play(page) -> bool:
    """尽力点一下播放按钮（不同课程用的播放器皮肤不一样）。"""
    selectors = [
        "[aria-label='播放按钮']", ".xg-start-inner", ".xgplayer-start", "xg-start",
        ".player-container", ".vjs-big-play-button", ".prism-big-play-btn",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=1500, force=True)
                return True
        except Exception:
            continue
    return False


def _classify(body: str) -> str:
    """播放列表正文类型：plain-master / plain-media / confused / other"""
    if not body:
        return "other"
    head = body.lstrip("\ufeff \r\n\t")
    if head.startswith("#EXTM3U"):
        return "plain-master" if "#EXT-X-STREAM-INF" in body else "plain-media"
    if head.startswith("[xiaoe]") or head.startswith("%5Bxiaoe%5D"):
        return "confused"
    return "other"


def capture_streams(url: str, profile: Path | None = None, wait_seconds: int = 90,
                    login_seconds: int = 240, log=_log) -> dict:
    """打开课程页并监听网络请求，捕获 m3u8 与分片样本。

    返回 dict：source_url / final_url / title / m3u8 / ts_samples / bodies / cookies
    """
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    profile = Path(profile or DEFAULT_PROFILE)
    headless = os.environ.get("XET_HEADLESS") == "1"
    m3u8_hits: list[str] = []
    seg_hits: list[str] = []
    api_hits: list[str] = []

    def on_request(req):
        u = req.url
        # 必须按 path 判断类型：小鹅通的 KPI 上报接口会把播放地址塞进 query
        # （如 alivereport.xiaoeknow.com/report/alive_information?...play_url=...m3u8），
        # 用整条 URL 做子串匹配会把上报请求误当成播放列表。
        path = urlparse(u).path.lower()
        if path.endswith(".m3u8"):
            if u not in m3u8_hits:
                m3u8_hits.append(u)
                log(f"[捕获] m3u8: {u[:150]}")
        elif path.endswith((".ts", ".m4s")):
            if len(seg_hits) < 3 and u not in seg_hits:
                seg_hits.append(u)
                log(f"[捕获] 分片: {u[:150]}")
        elif any(k in u.lower() for k in ("/play", "get_media", "video_url", "playurl")):
            if len(api_hits) < 20 and u not in api_hits:
                api_hits.append(u)

    result = {"source_url": url, "final_url": url, "title": "", "m3u8": [],
              "ts_samples": [], "bodies": {}, "candidate_api": []}

    with sync_playwright() as p:
        ctx = _launch_ctx(p, profile, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("request", on_request)
            log(f"[导航] {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"[导航] 警告: {type(e).__name__}: {e}")
            page.wait_for_timeout(2500)
            log(f"[状态] url={page.url}")
            try:
                log(f"[状态] title={page.title()}")
            except Exception:
                pass

            need_login = any(k in page.url for k in ("login", "open.weixin", "/auth"))
            if need_login:
                log("[登录] 需要微信扫码登录，请在弹出的浏览器窗口里完成"
                    f"（最多等 {login_seconds} 秒，登录态会记在 profile 里长期复用）")

            deadline = time.time() + (login_seconds if need_login else wait_seconds)
            last_click = 0.0
            while time.time() < deadline:
                if m3u8_hits:
                    page.wait_for_timeout(2500)  # 再等一会，收全主/子播放列表
                    break
                if time.time() - last_click > 12:
                    if _click_play(page):
                        last_click = time.time()
                page.wait_for_timeout(2000)
                if need_login and not any(k in page.url for k in ("login", "open.weixin", "/auth")):
                    log(f"[登录] 已跳出登录页 -> {page.url}")
                    need_login = False
                    deadline = time.time() + wait_seconds

            result.update(title=_safe_title(page.title()), final_url=page.url,
                          m3u8=list(m3u8_hits), ts_samples=list(seg_hits),
                          candidate_api=api_hits)

            # 播放列表签名只有约 10 分钟有效期 —— 必须立刻抓正文，不能等
            if m3u8_hits:
                for u in m3u8_hits[:4]:
                    try:
                        r = ctx.request.get(u, timeout=15000)
                        result["bodies"][u] = r.text()[:8000]
                    except Exception as e:
                        result["bodies"][u] = f"ERR {type(e).__name__}: {e}"
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return result


def _safe_title(t: str) -> str:
    t = (t or "").strip()
    # 去掉站点后缀与常见分隔符，标题会更干净（如「xxx - 小鹅通」）
    t = re.sub(r"\s*[-–—_|]\s*(小鹅通|xiaoe.*|小鹅通课堂.*)$", "", t, flags=re.I)
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", t)
    return t.strip(" ._")[:80]


# ==================== 分片枚举（混淆播放列表的正解） ====================
def _split_seg_url(u: str):
    """把分片 URL 拆成 {base, width, ext, query, index}。

    关于 padding（这里踩过坑）：**不能**用捕获到的序号长度当补零宽度。
    小鹅通的实际文件是 `v.f146750_277.ts`，若按 len("277")=3 补零，
    序号 0 会被拼成 `v.f146750_000.ts` —— 这个文件不存在。
    正确规则：**只有捕获到的序号本身以 0 开头（如 `_00042.ts`）才认为有补零**，
    否则宽度记 0，按原始十进制拼接。
    """
    m = re.match(r"^(.*?)(\d+)(\.[A-Za-z0-9]{2,5})(\?.*)?$", u)
    if not m:
        return None
    digits = m.group(2)
    return {
        "base": m.group(1),
        "width": len(digits) if digits.startswith("0") else 0,
        "ext": m.group(3),
        "query": m.group(4) or "",
        "index": int(digits),
    }


def _seg_url(parts: dict, i: int) -> str:
    num = str(i).zfill(parts["width"]) if parts["width"] else str(i)
    return f"{parts['base']}{num}{parts['ext']}{parts['query']}"


def _probe(sess, parts, i: int, headers: dict, timeout=15) -> bool:
    """判断序号 i 是否存在。HEAD 优先，CDN 不支持则回退 Range GET。"""
    if i < 0:
        return False
    u = _seg_url(parts, i)
    try:
        r = sess.head(u, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 206) and "text/html" not in r.headers.get("content-type", ""):
            return True
        if r.status_code in (403, 404, 410):
            return False
    except Exception:
        pass
    try:
        r = sess.get(u, headers={**headers, "Range": "bytes=0-1023"}, timeout=timeout)
        return r.status_code in (200, 206) and len(r.content) > 0 \
            and "text/html" not in r.headers.get("content-type", "")
    except Exception:
        return False


def find_bounds(parts, idx: int, headers: dict, log=_log, cap: int = 1 << 21) -> tuple[int, int]:
    """从已知存在的序号 idx 出发，指数探测 + 二分，返回闭区间 [lo, hi]。"""
    import requests
    sess = requests.Session()
    if not _probe(sess, parts, idx, headers):
        raise RuntimeError(f"分片样本 {idx} 探测失败：签名可能已过期，请重新捕获")

    hi_ok, step = idx, 1
    while step < cap and _probe(sess, parts, idx + step, headers):
        hi_ok = idx + step
        step *= 2
    hi_bad = idx + step
    while hi_bad - hi_ok > 1:
        mid = (hi_ok + hi_bad) // 2
        if _probe(sess, parts, mid, headers):
            hi_ok = mid
        else:
            hi_bad = mid
    upper = hi_ok

    lo_ok, step = idx, 1
    while lo_ok - step >= 0 and _probe(sess, parts, lo_ok - step, headers):
        lo_ok -= step
        step *= 2
    lo_bad = lo_ok - step
    while lo_ok - lo_bad > 1:
        mid = (lo_ok + lo_bad) // 2
        if _probe(sess, parts, mid, headers):
            lo_ok = mid
        else:
            lo_bad = mid
    lower = max(0, lo_ok)

    log(f"[枚举] 分片序号区间 {lower} ~ {upper}（共 {upper - lower + 1} 片）")
    return lower, upper


def download_segments(parts, lo: int, hi: int, segdir: Path, headers: dict,
                      workers: int = DEFAULT_WORKERS, log=_log) -> int:
    """并发下载 [lo, hi] 全部分片，返回成功数。403 视为签名过期并停止新增。"""
    import requests

    segdir.mkdir(parents=True, exist_ok=True)
    total = hi - lo + 1
    stop = {"v": False}
    done = {"ok": 0, "fail": 0}
    t0 = time.time()

    def fetch(i: int, attempt: int = 0) -> tuple[int, str]:
        if stop["v"]:
            return i, "skip"
        p = segdir / f"{i - lo:06d}.ts"
        if p.exists() and p.stat().st_size > 10_000:
            return i, "cached"
        u = _seg_url(parts, i)
        try:
            r = requests.get(u, headers=headers, timeout=45)
            if r.status_code == 403:
                stop["v"] = True
                return i, "403-EXPIRED"
            if r.status_code != 200 or len(r.content) < 1000:
                raise RuntimeError(f"HTTP {r.status_code} len={len(r.content)}")
            tmp = p.with_suffix(".part")
            tmp.write_bytes(r.content)
            os.replace(tmp, p)
            return i, "ok"
        except Exception as e:
            if attempt < 3 and not stop["v"]:
                time.sleep(1.0 + attempt)
                return fetch(i, attempt + 1)
            return i, f"FAIL {type(e).__name__}: {e}"

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch, i) for i in range(lo, hi + 1)]
        for n, f in enumerate(cf.as_completed(futs), 1):
            i, st = f.result()
            if st in ("ok", "cached"):
                done["ok"] += 1
            elif st == "skip":
                pass
            else:
                done["fail"] += 1
                log(f"[下载] {n}/{total} 分片 {i}: {st}")
                if st == "403-EXPIRED":
                    log("!! 签名已过期 —— 需要重新捕获播放地址")
                    break
            if n % 100 == 0:
                el = max(time.time() - t0, 1e-6)
                log(f"[下载] {n}/{total} ok={done['ok']} fail={done['fail']} "
                    f"{el:.0f}s ({n / el:.1f} 片/秒)")

    el = time.time() - t0
    log(f"[下载] 结束 ok={done['ok']} fail={done['fail']} 用时 {el:.0f}s")
    return done["ok"]


def verify_segments(segdir: Path, log=_log) -> tuple[int, int]:
    """抽样校验 TS 同步字节（0x47），返回 (文件数, 非TS数)。"""
    files = sorted(segdir.glob("*.ts"))
    bad = 0
    sample = files[:3] + files[len(files) // 2:len(files) // 2 + 3] + files[-3:]
    for f in sample:
        try:
            if f.open("rb").read(1) != b"\x47":
                bad += 1
        except Exception:
            bad += 1
    if files:
        sz = sum(f.stat().st_size for f in files) / 1024 / 1024
        log(f"[校验] {len(files)} 分片 / {sz:.1f} MB；抽样 {len(sample)} 个，非 TS 同步字节 {bad} 个")
    return len(files), bad


def merge_segments(segdir: Path, out: Path, log=_log) -> Path:
    """ffmpeg concat demuxer 无损拼接（不重编码）。"""
    segs = sorted(segdir.glob("*.ts"))
    if not segs:
        raise RuntimeError("没有可拼接的分片")
    lst = out.parent / "_concat_list.txt"
    with lst.open("w", encoding="utf-8", newline="\n") as f:
        for s in segs:
            f.write("file '" + s.as_posix() + "'\n")
    ff = find_ffmpeg()
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "warning",
           "-f", "concat", "-safe", "0", "-i", str(lst),
           "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out)]
    log(f"[拼接] {len(segs)} 片 → {out.name}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg 拼接失败:\n{(r.stderr or '')[-1500:]}")
    log(f"[拼接] 完成 {out.stat().st_size / 1024 / 1024:.1f} MB")
    try:
        lst.unlink()
    except Exception:
        pass
    return out


# ==================== 明文播放列表：交给 ffmpeg ====================
def download_playlist_ffmpeg(m3u8_url: str, out: Path, referer: str = "",
                             log=_log) -> Path:
    """ffmpeg 直读 m3u8（自带 AES-128 解密与 fMP4 处理）。"""
    ff = find_ffmpeg()
    hdr = f"Referer: {referer}\r\n" if referer else ""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "warning",
           "-user_agent", UA]
    if hdr:
        cmd += ["-headers", hdr]
    cmd += ["-i", m3u8_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out)]
    log(f"[ffmpeg] 拉取 HLS 播放列表 → {out.name}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg 拉流失败:\n{(r.stderr or '')[-1500:]}")
    log(f"[ffmpeg] 完成 {out.stat().st_size / 1024 / 1024:.1f} MB")
    return out


# ==================== 对外主入口 ====================
def download_xet(url: str, outdir, name_hint: str | None = None,
                 workers: int | None = None, profile=None,
                 keep_segments: bool = False, log=_log) -> Path:
    """小鹅通课程页 → 下载 mp4，返回文件路径。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    workers = workers or DEFAULT_WORKERS

    cap = capture_streams(url, profile=profile, log=log)
    title = _safe_title(cap.get("title") or "") or _safe_title(name_hint or "") or "xet_video"
    log(f"[标题] {title}")

    m3u8s = cap.get("m3u8") or []
    bodies = cap.get("bodies") or {}
    kinds = {u: _classify(bodies.get(u, "")) for u in m3u8s}

    # ---- 路径 1：明文播放列表 → ffmpeg ----
    masters = [u for u in m3u8s if kinds.get(u) == "plain-master"]
    medias = [u for u in m3u8s if kinds.get(u) == "plain-media"]
    if masters or medias:
        target = masters[0] if masters else medias[-1]
        log(f"[路径] 明文播放列表（{kinds[target]}），交给 ffmpeg")
        out = outdir / f"{title}.mp4"
        return download_playlist_ffmpeg(target, out, referer=cap.get("final_url", ""), log=log)

    # ---- 路径 2：混淆播放列表 → 分片枚举 ----
    confused = [u for u in m3u8s if kinds.get(u) == "confused"]
    if confused:
        log("[路径] 检测到 confusion_index 混淆播放列表 —— 不解密，走分片枚举")
    seg_samples = cap.get("ts_samples") or []
    if not seg_samples:
        hint = ""
        if not m3u8s:
            hint = ("\n既没捕获到 m3u8 也没捕获到分片，常见原因：\n"
                    "  - 未登录 / 未开通该课程（登录态会保存在 profile 里，重跑即可）\n"
                    "  - 直播尚未生成回放\n"
                    "  - 页面需要手动点一下播放按钮")
        raise RuntimeError("未捕获到任何视频流地址。" + hint)

    parts = _split_seg_url(seg_samples[0])
    if not parts:
        raise RuntimeError(f"分片 URL 无法解析出序号模式: {seg_samples[0][:200]}")
    log(f"[模式] {_seg_url(parts, 0)[:160]}")

    headers = {"User-Agent": UA, "Referer": cap.get("final_url") or url}
    idx = parts["index"]
    lo, hi = find_bounds(parts, idx, headers, log=log)

    est = hi - lo + 1
    if est > 20000:
        raise RuntimeError(f"探测到 {est} 个分片，明显不合理，已中止（请重新捕获）")

    segdir = outdir / "_segments"
    log(f"[计划] 并发 {workers} 线程下载 {est} 片")
    download_segments(parts, lo, hi, segdir, headers, workers=workers, log=log)

    n, bad = verify_segments(segdir, log=log)
    if n < est:
        log(f"!! 分片不齐（{n}/{est}），拼接结果可能缺尾部；可重跑续传")
    if bad > 0:
        log(f"!! 抽样发现 {bad} 个分片不是 TS —— 可能开了分片加密，产物或不可播")

    out = outdir / f"{title}.mp4"
    merge_segments(segdir, out, log=log)

    if not keep_segments:
        try:
            shutil.rmtree(segdir)
            log("[清理] 分片缓存已删除")
        except Exception as e:
            log(f"[清理] 分片缓存删除失败（可手动删）: {e}")
    return out


def download_hls(m3u8_url: str, outdir, name_hint: str | None = None, log=_log) -> Path:
    """用户直接给出 m3u8 地址时的入口。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    name = _safe_title(name_hint or "") or _safe_title(
        Path(urlparse(m3u8_url).path).stem) or "hls_video"
    out = outdir / f"{name}.mp4"
    referer = f"{urlparse(m3u8_url).scheme}://{urlparse(m3u8_url).netloc}/"
    return download_playlist_ffmpeg(m3u8_url, out, referer=referer, log=log)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="小鹅通课程视频下载器")
    ap.add_argument("url", help="课程页 URL 或 m3u8 地址")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 ./xet_out）")
    ap.add_argument("--workers", type=int, default=None, help=f"并发数（默认 {DEFAULT_WORKERS}）")
    ap.add_argument("--profile", default=None, help=f"浏览器 profile 目录（默认 {DEFAULT_PROFILE}）")
    ap.add_argument("--capture-only", action="store_true", help="只捕获流地址，不下载")
    ap.add_argument("--keep-segments", action="store_true", help="保留下载的分片缓存")
    a = ap.parse_args()

    outdir = Path(a.out_dir or "./xet_out")
    if a.capture_only:
        cap = capture_streams(a.url, profile=a.profile, log=_log)
        cap.pop("bodies", None)
        _log(json.dumps(cap, ensure_ascii=False, indent=2))
        return 0
    if ".m3u8" in a.url:
        out = download_hls(a.url, outdir)
    else:
        out = download_xet(a.url, outdir, workers=a.workers, profile=a.profile,
                           keep_segments=a.keep_segments)
    _log(f"完成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
