"""ltaoo Bridge 客户端 —— 调用自部署的 Bridge Worker 解析视频号链接，调用方无需元宝 Cookie。

详见上游文档 docs/feature/bridge.md。核心事实（已核实源码契约）：
  - Bridge 是「自部署」Cloudflare Worker（Durable Objects），无公共开放注册；
    作者示例域名 dm-bridge.litao.workers.dev 仅为文档演示，不可用。
  - Bridge 仍需一台「设备」（运行 wx_channels_download 并以 bridge 模式连入、视频号页面已连接）
    提供真实抓取能力——即仍需某个 WeChat 会话，只是调用方不再碰 cookie。
  - 调用方用 Bearer Call Token 鉴权；同步 /v1/invoke 最长等待 10s 直接返回结果，
    超时使用 /v1/call 异步创建任务 + 轮询 /v1/tasks/:id。

已核实的精确契约：
  GET  /health                 健康检查（免鉴权）
  GET  /v1                     查询 devices / 在线 methods / 任务统计
  GET  /v1/credits             查询当前 Token 积分余额
  POST /v1/invoke  {method, args, target_device_id?, idempotency_key?}
                               同步调用；200 时响应体即设备方法直接返回的 JSON（根节点，无 data/task 包裹）
  POST /v1/call    {method, args, idempotency_key?}
                               异步创建任务；201 返回 {task:{id,status,...}}；结果在 task.result
  GET  /v1/tasks/:id           轮询任务；completed 时结果在 task.result

配置（走 secret_store，禁止明文落技能目录）：
  BRIDGE_URL         自部署 Bridge 的 Worker 地址，如 https://dm-bridge.xxx.workers.dev
  BRIDGE_CALL_TOKEN  管理页创建的调用 Token

激活方式：在主流程设置环境变量 WX_SPH_MODE=bridge（且 BRIDGE_URL/TOKEN 就绪），
process_sph 会自动走本模块而非本地 sph worker。
"""

import os
import time
import uuid
from pathlib import Path

import httpx

_BRIDGE_TIMEOUT = 15          # 客户端 HTTP 超时（invoke 服务端硬上限 10s）
_ASYNC_POLL_MAX = 90          # 异步任务最长轮询秒数
_ASYNC_POLL_INTERVAL = 2       # 轮询间隔秒
_SECRETS_ENV = Path.home() / ".workbuddy" / ".secrets.env"


def _read_secret(name: str) -> str:
    """环境变量优先，回退 ~/.workbuddy/.secrets.env（与 secret_store 一致，无长度限制）。"""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if _SECRETS_ENV.exists():
        try:
            for line in _SECRETS_ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                if k.strip() == name:
                    return val.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def bridge_url() -> str:
    return _read_secret("BRIDGE_URL").rstrip("/")


def bridge_token() -> str:
    return _read_secret("BRIDGE_CALL_TOKEN")


def _headers() -> dict:
    return {"Authorization": f"Bearer {bridge_token()}", "Content-Type": "application/json"}


def bridge_status() -> dict:
    """GET /v1 —— 返回设备/在线方法/任务统计，也用于探测可用性与发现解析方法。"""
    r = httpx.get(f"{bridge_url()}/v1", headers=_headers(), timeout=_BRIDGE_TIMEOUT)
    r.raise_for_status()
    return r.json()


def bridge_health() -> bool:
    """GET /health —— 免鉴权健康检查。"""
    try:
        r = httpx.get(f"{bridge_url()}/health", timeout=_BRIDGE_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _discover_sph_method(status: dict) -> str:
    """从 /v1 返回的在线方法中挑出能解析视频号链接的方法。

    设备以 methods:'auto' 注册时，方法名通常镜像本地 API 路径段
    （如 channels.parse_sph / channels.fetch_sph）。这里不硬编码，运行时按关键字发现：
    要求同时含 sph 与（parse 或 channels 或 fetch），避免误匹配其他 channels 方法。"""
    haystack = []
    for key in ("methods", "onlineMethods", "devices"):
        val = status.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    haystack.append(item)
                elif isinstance(item, dict):
                    for m in item.get("methods", []) or []:
                        if isinstance(m, str):
                            haystack.append(m)
                    if item.get("method"):
                        haystack.append(str(item["method"]))
    # 候选：必须含 sph，且含 parse/channels/fetch 之一
    for m in haystack:
        ml = m.lower()
        if "sph" in ml and any(k in ml for k in ("parse", "channel", "fetch")):
            return m
    # 兜底：松匹配（仅含 sph）
    for m in haystack:
        if "sph" in m.lower():
            return m
    # 兜底：已知常见命名
    for cand in ("channels.parse_sph", "parse_sph", "channels.fetch_sph", "fetch_sph"):
        if cand in haystack:
            return cand
    raise RuntimeError(f"Bridge 未找到可解析视频号的方法；在线方法列表：{haystack[:30]}")


def _invoke(method: str, args: dict) -> dict:
    """POST /v1/invoke —— 同步调用（服务端最多等待 10s）。返回设备方法的根节点结果。"""
    r = httpx.post(
        f"{bridge_url()}/v1/invoke",
        headers=_headers(),
        json={"method": method, "args": args or {}},
        timeout=_BRIDGE_TIMEOUT,
    )
    if r.status_code == 402:
        raise RuntimeError("Bridge 积分不足（402 Payment Required），请在管理页充值调用 Token。")
    if r.status_code == 409:
        raise RuntimeError("Bridge 设备未注册该方法（409），请确认设备已连入且 methods 含解析方法。")
    if r.status_code == 504:
        # 服务端 10s 超时 → 交由调用方降级到异步
        raise RuntimeError("invoke timed out after 10 seconds")
    r.raise_for_status()
    data = r.json()
    # 部分实现即使 200 也可能在 body 带 error
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Bridge 调用返回错误：{data['error']}")
    return data


def _call_async(method: str, args: dict) -> dict:
    """POST /v1/call —— 异步创建任务，然后轮询 /v1/tasks/:id 直到完成。

    返回 task.result（设备方法的根节点结果）。视频解析常 >10s，故优先走这条更稳。"""
    payload = {
        "method": method,
        "args": args or {},
        "idempotency_key": f"sph-{uuid.uuid4().hex[:16]}",
    }
    r = httpx.post(
        f"{bridge_url()}/v1/call",
        headers=_headers(),
        json=payload,
        timeout=_BRIDGE_TIMEOUT,
    )
    if r.status_code == 402:
        raise RuntimeError("Bridge 积分不足（402），请在管理页充值调用 Token。")
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Bridge 创建任务失败（HTTP {r.status_code}）：{r.text[:200]}")
    task = r.json().get("task", {})
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError(f"Bridge 创建任务未返回 task.id：{r.text[:200]}")
    deadline = time.time() + _ASYNC_POLL_MAX
    while time.time() < deadline:
        time.sleep(_ASYNC_POLL_INTERVAL)
        tr = httpx.get(f"{bridge_url()}/v1/tasks/{task_id}", headers=_headers(), timeout=_BRIDGE_TIMEOUT)
        if tr.status_code != 200:
            continue
        t = tr.json().get("task", {})
        status = t.get("status")
        if status == "completed":
            return t.get("result")
        if status == "failed":
            raise RuntimeError(f"Bridge 任务执行失败：{t.get('error')}")
        # 其他中间态（queued/assigned/running）继续轮询
    raise RuntimeError(f"Bridge 任务轮询超时（>{_ASYNC_POLL_MAX}s），task_id={task_id}")


def _normalize(result) -> dict:
    """把设备返回的不同结构归一化为 {author, description, video_url}。

    兼容三类：
      1) 本地 API 风格：{code:0, data:{data:{feedInfo:{h264VideoInfo:{videoUrl}}, authorInfo:{nickname}}}}
      2) 略扁平：{data:{feedInfo:{...}, authorInfo:{...}}}
      3) 直出风格：{videoUrl|url, author, description}
      4) 异步任务可能把结果再包一层 {url:...} / {result:{...}}
    """
    if isinstance(result, str):
        # 极少数实现直接返回直链字符串
        if result.startswith("http"):
            return {"author": "", "description": "", "video_url": result}
        raise RuntimeError(f"Bridge 返回非 URL 字符串：{result[:200]}")
    if not isinstance(result, dict):
        raise RuntimeError(f"Bridge 返回结构无法识别：{str(result)[:200]}")

    # 异步任务可能把真实结果放在 result.result（二级）
    if "result" in result and isinstance(result["result"], (dict, str)) and "video_url" not in result and "url" not in result:
        return _normalize(result["result"])

    # 提取 feedInfo / authorInfo
    feed = None
    author_info = {}
    if "data" in result and isinstance(result["data"], dict):
        inner = result["data"].get("data", result["data"])
        if isinstance(inner, dict):
            feed = inner.get("feedInfo", inner)
            author_info = inner.get("authorInfo", {}) or {}
    feed = feed if isinstance(feed, dict) else None

    video_url = None
    author = ""
    description = ""
    if feed:
        h264 = feed.get("h264VideoInfo", {}) or {}
        video_url = h264.get("videoUrl") or feed.get("videoUrl") or feed.get("url")
        author = (author_info.get("nickname", "")) if isinstance(author_info, dict) else ""
        description = feed.get("description", "")
    # 直出风格兜底
    if not video_url:
        video_url = result.get("videoUrl") or result.get("url")
        author = result.get("author", author)
        description = result.get("description", description)
    if not video_url:
        raise RuntimeError(f"Bridge 返回中找不到视频直链：{str(result)[:300]}")
    return {"author": author or "", "description": description or "", "video_url": video_url}


def parse_sph_via_bridge(url: str) -> dict:
    """端到端：发现解析方法 → 调用（invoke 优先，超时降级 call+轮询）→ 归一化。
    返回 {author, description, video_url}；失败抛 RuntimeError。"""
    if not bridge_url() or not bridge_token():
        raise RuntimeError("Bridge 未配置：请设置 BRIDGE_URL 与 BRIDGE_CALL_TOKEN（~/.workbuddy/.secrets.env）。")
    try:
        status = bridge_status()
    except Exception as e:
        raise RuntimeError(f"Bridge 状态查询失败（{type(e).__name__}: {e}）；请确认 URL 可达且 Token 有效。")
    method = _discover_sph_method(status)
    # 同步优先，超时（含 504 / 10s）降级异步
    try:
        result = _invoke(method, {"url": url})
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg or "504" in msg or "timeout" in msg.lower():
            log_bridge(f"[Bridge] 同步 invoke 超时，降级异步 /v1/call：{method}")
            result = _call_async(method, {"url": url})
        else:
            raise
    except Exception as e:
        raise RuntimeError(f"Bridge 调用 {method} 失败：{e}")
    return _normalize(result)


def log_bridge(msg: str):
    """轻量日志：尽量复用主脚本 log，缺失时降级 print。"""
    try:
        import multi_media_processor as mmp  # noqa
        if hasattr(mmp, "log"):
            mmp.log(msg)
            return
    except Exception:
        pass
    print(msg)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(parse_sph_via_bridge(sys.argv[1]))
    else:
        print("usage: bridge_client.py <sph_url>")
