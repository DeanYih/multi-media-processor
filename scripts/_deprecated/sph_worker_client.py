"""视频号解析：自部署 sph Worker 客户端（调用方免元宝 Cookie）。

契约来自 ltaoo/wx_channels_download 的 internal/workers/sph/worker.js（逐字核对）：
  POST {WX_SPH_WORKER_URL}/api/fetch_video_profile
  Authorization: Bearer <ACCESS_CREDENTIAL>      # 部署时设的 env.ACCESS_CREDENTIAL
  Content-Type: application/json
  Body: {"url": "<视频号分享链接>"}

服务端用 env.COOKIE（部署时配置的元宝 Cookie，secret binding）去请求：
  - https://yuanbao.tencent.com/api/weixin/get_parse_result
  - https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info
并把 getFeedInfo 的原始 JSON 直透给调用者：
  {"errCode":0, "data": {"feedInfo": {"h264VideoInfo": {"videoUrl": "..."},
                                     "description": "...",
                                     "authorInfo": {"nickname": "..."}}}}

要点：调用方（本技能）完全不需要元宝 Cookie，只需部署时设的一次性 ACCESS_CREDENTIAL。
"""
import json
import os
from pathlib import Path

try:
    import httpx
except Exception:  # pragma: no cover - 依赖缺失时给出友好错误，而非 import 崩溃
    httpx = None

SECRET_MODULE = Path.home() / ".workbuddy" / "lib" / "secret_store.py"


def _load_secret_store():
    if SECRET_MODULE.exists():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("secret_store", str(SECRET_MODULE))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
        except Exception:
            return None
    return None


def _get(name, required=False):
    v = (os.environ.get(name, "") or "").strip()
    if v:
        return v
    ss = _load_secret_store()
    if ss is not None:
        try:
            v = (ss.get_secret(name) or "").strip()
            if v:
                return v
        except Exception:
            pass
    return ""


def worker_available() -> bool:
    return bool(_get("WX_SPH_WORKER_URL")) and bool(_get("WX_SPH_WORKER_TOKEN"))


def _extract(obj, *keys):
    """递归在嵌套 dict/list 中找第一个命中任一 key（大小写不敏感）的非空 str 值。"""
    if isinstance(obj, dict):
        lower = {k.lower(): k for k in obj.keys()}
        for k in keys:
            lk = k.lower()
            if lk in lower:
                val = obj[lower[lk]]
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, (dict, list)):
                    r = _extract(val, *keys)
                    if r:
                        return r
        for v in obj.values():
            r = _extract(v, *keys)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _extract(item, *keys)
            if r:
                return r
    return ""


def _extract_feed(result):
    """从 worker 直透的上游 JSON 提取视频号核心字段。"""
    if not isinstance(result, dict):
        return "", "", "", ""
    data = result.get("data")
    # 兼容两种包裹：{data:{feedInfo}} 或 {data:{data:{feedInfo}}}
    if isinstance(data, dict) and "feedInfo" not in data:
        data = data.get("data", data)
    feed_obj = (data or {}).get("feedInfo", {}) if isinstance(data, dict) else {}
    if not isinstance(feed_obj, dict):
        feed_obj = {}
    video_url = (_extract(feed_obj, "videoUrl", "playUrl") or _extract(result, "videoUrl", "playUrl"))
    desc = _extract(feed_obj, "description") or _extract(result, "description")
    author = (_extract(feed_obj, "nickname", "authorName")
              or _extract(result, "nickname", "authorName"))
    cover = (_extract(feed_obj, "coverUrl", "cover") or _extract(result, "coverUrl", "cover"))
    return (video_url or ""), (desc or ""), (author or ""), (cover or "")


def parse(url: str):
    """调用 sph Worker 解析视频号分享链接。

    返回 (video_url, description, author, cover)。
    失败时抛出 RuntimeError（含上游/鉴权错误信息，便于上层 sys.exit 提示）。
    """
    if httpx is None:
        raise RuntimeError("httpx 未安装，sph Worker 模式不可用（请 pip install httpx）")
    base = _get("WX_SPH_WORKER_URL").rstrip("/")
    token = _get("WX_SPH_WORKER_TOKEN")
    if not base or not token:
        raise RuntimeError("WX_SPH_WORKER_URL / WX_SPH_WORKER_TOKEN 未配置"
                           "（写入 ~/.workbuddy/.secrets.env）")
    api = base + "/api/fetch_video_profile"
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }
    body = json.dumps({"url": url}, ensure_ascii=False).encode("utf-8")
    try:
        resp = httpx.post(api, content=body, headers=headers, timeout=30)
    except Exception as e:
        raise RuntimeError(f"sph Worker 请求失败（网络/地址错误）: {e}")
    if resp.status_code == 401:
        raise RuntimeError("sph Worker 鉴权失败：WX_SPH_WORKER_TOKEN 与部署时 ACCESS_CREDENTIAL 不一致")
    if resp.status_code == 503:
        raise RuntimeError("sph Worker 未配置 ACCESS_CREDENTIAL（部署时未设凭证）")
    if resp.status_code != 200:
        raise RuntimeError(f"sph Worker HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        result = resp.json()
    except Exception:
        raise RuntimeError(f"sph Worker 返回非 JSON: {resp.text[:300]}")
    # worker 直透上游；上游失败时 errCode != 0（部分情况无 errCode 但有 error 字段）
    err_code = result.get("errCode") if isinstance(result, dict) else None
    upstream_ok = (err_code in (0, None)) or (
        isinstance(result, dict) and "feedInfo" in json.dumps(result)
    )
    if not upstream_ok:
        msg = (result.get("error") or result.get("msg")
               or json.dumps(result, ensure_ascii=False)[:300])
        raise RuntimeError(f"sph Worker 上游解析失败: {msg}")
    video_url, desc, author, cover = _extract_feed(result)
    if not video_url or not video_url.startswith("http"):
        raise RuntimeError(f"sph Worker 未返回可用 videoUrl："
                           f"{json.dumps(result, ensure_ascii=False)[:300]}")
    return video_url, desc, author, cover
