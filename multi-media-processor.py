#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multi-media-processor.py — 微信视频号/公众号链接一键处理

职责：
  - 微信视频号(sph) → 解析原地址 → 下载 → Whisper 本地转写
  - 微信公众号(mp) → 抓取正文
  - B站/YouTube/小红书/TikTok/微博/Dailymotion/Vimeo → 视频下载 + Whisper 转写

依赖：Python 3.8+ 、httpx、yt-dlp、ffmpeg、whisper CLI、requests
"""
import argparse, os, re, sys, json, time, socket, subprocess, shutil, warnings, importlib.util
from pathlib import Path
from urllib.parse import quote, unquote
from typing import Optional, List, Dict

warnings.filterwarnings('ignore')


def safe_slug(s: str, maxlen: int = 60) -> str:
    """把任意字符串转成安全的文件名片段：去除路径非法字符、折叠空白。
    用于避免短链 URL / 视频标题含 \\/:*?\"<>| 等字符导致 Windows 下 WinError 123。"""
    if not s:
        return ""
    for ch in '\\/:*?"<>|\t\n\r':
        s = s.replace(ch, " ")
    s = re.sub(r"\s+", "_", s.strip())
    s = s.strip("._-")
    return s[:maxlen]


# ---------------- 跨平台 Python 环境检测 ----------------
def _find_python() -> Optional[str]:
    """通用 Python 检测：优先 sys.executable，其次 PATH，最后常见安装路径。
    兼容 Hermes、WorkBuddy、Cursor、Copilot、独立脚本等多种运行环境。"""
    # 1. 当前进程使用的 Python（最可靠）
    cur = sys.executable
    if cur and os.path.isfile(cur):
        return cur

    # 2. WorkBuddy 专属路径（向后兼容）
    wb_paths = []
    for suffix in ["Scripts/python.exe", "bin/python"]:
        p = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / suffix
        if p.exists():
            wb_paths.append(str(p))
    if wb_paths:
        return wb_paths[0]

    # 3. PATH 中的 python3 / python
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found

    # 4. Windows 常见安装位置
    if os.name == "nt":
        candidates = [
            Path("C:/Python311/python.exe"),
            Path("C:/Python310/python.exe"),
            Path("C:/Python39/python.exe"),
            Path("C:/Python38/python.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python310" / "python.exe",
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    return None


def _ensure_python_exists() -> Optional[str]:
    """确保 Python 可用，返回 Python 路径；不可用返回 None。"""
    py = _find_python()
    if not py:
        print("[错误] 未找到 Python 解释器，请安装 Python 3.8+ 并加入 PATH", flush=True)
        return None
    # 验证可执行
    try:
        result = subprocess.run([py, "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return py
    except Exception:
        pass
    print(f"[警告] Python 验证失败: {py}", flush=True)
    return None


_PYTHON_BIN = _ensure_python_exists()


def log(*a):
    print(*a, flush=True)


def _silent_run(cmd, **kwargs):
    """静默运行子进程：Windows 隐藏窗口，所有输出丢弃"""
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    if os.name == "nt":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return subprocess.run(cmd, **kwargs)


def _silent_check_call(cmd, **kwargs):
    """静默检查调用，等价于 check_call 但无窗口"""
    return _silent_run(cmd, check=True, **kwargs)


def ensure_python_deps():
    pkgs = {"httpx": "httpx", "yt-dlp": "yt_dlp",
            "requests": "requests", "zhconv": "zhconv", "jieba": "jieba"}
    missing = []
    for pip_name, mod_name in pkgs.items():
        spec = importlib.util.find_spec(mod_name)
        if spec is None:
            missing.append(pip_name)
    if not missing:
        return
    # 使用当前 Python 的 pip 安装依赖
    pip_cmd = [_PYTHON_BIN or sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    pip_cmd.extend(missing)
    log(f"正在安装缺失的 Python 依赖: {', '.join(missing)} ...")
    try:
        subprocess.run(pip_cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("✅ 依赖安装完成")
    except subprocess.CalledProcessError as e:
        log(f"⚠️ pip 安装失败（{e}），请手动运行: pip install {' '.join(missing)}")
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    log(f"[依赖] 首次运行，自动安装 Python 包: {', '.join(missing)}")
    try:
        _silent_check_call([sys.executable, "-m", "pip", "install", *missing])
    except Exception as e:
        sys.exit(f"[依赖] 自动安装失败: {e}\n请手动运行: pip install {' '.join(missing)}")
    for mod_name in pkgs.values():
        importlib.import_module(mod_name)


ensure_python_deps()

import httpx
import yt_dlp
import requests

# ---------------- 自动推导路径 ----------------
HERE = Path(__file__).resolve()
SKILL_DIR = HERE.parent.parent
BIN = SKILL_DIR / "bin"
TOOL_DIR = BIN / "wx_channels_download"
WHISPER_DIR = BIN / "whisper"
_WHISPER_EXT = ".exe" if os.name == "nt" else ""
WHISPER_CLI = WHISPER_DIR / "bin" / f"whisper-cli{_WHISPER_EXT}"
WHISPER_WORK = WHISPER_DIR / ".work"

# wx_channels_download API
API_BASE = "http://127.0.0.1:2022"
PARSE_SPH = API_BASE + "/api/channels/parse_sph"

def _save_work_dir(path: Path, *configs: Path) -> None:
    """把最终输出目录写入所有配置文件（用户级 + 技能内），写入失败静默忽略。"""
    for cfg in configs:
        try:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(str(path), encoding="utf-8")
        except Exception:
            pass


def _is_plausible_workspace(path: Path) -> bool:
    """判断目录是否像“WorkBuddy 工作区”：存在、非技能目录/系统目录/主目录本身。"""
    try:
        path = path.resolve()
    except Exception:
        return False
    if not path.is_dir():
        return False
    if path == SKILL_DIR or SKILL_DIR in path.parents:
        return False
    if path == Path.home().resolve():
        return False
    low = str(path).lower()
    for bad in (os.environ.get("WINDIR", r"C:\Windows").lower(),
                os.environ.get("TEMP", "").lower(),
                os.environ.get("TMP", "").lower()):
        if bad and low.startswith(bad):
            return False
    return True


def _decode_project_dir(name: str) -> Optional[Path]:
    """把 WorkBuddy 项目目录名反解为工作区路径（支持任意盘符）：
    e-Workbuddyarea                     -> E:\\Workbuddyarea
    c-Users-yourname-WorkBuddy-2026-08-26  -> C:\\Users\\yourname\\WorkBuddy（带唯一化时间戳）
    """
    m = re.match(r"^([a-zA-Z])-(.+?)(?:-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})?$", name)
    if not m:
        return None
    drive, rest = m.group(1).upper(), m.group(2)
    if os.name == "nt":
        candidate = Path(f"{drive}:\\{rest.replace('-', os.sep)}")
    else:
        candidate = Path("/" + rest.replace("-", "/"))
    try:
        return candidate.resolve() if candidate.is_dir() else None
    except Exception:
        return None


def _workspace_from_session() -> Optional[Path]:
    """通过 CODEBUDDY_SESSION_ID 精确定位当前会话所属工作区
    （会话 jsonl 文件存放在 ~/.workbuddy/projects/<工作区编码>/ 下）。"""
    sid = os.environ.get("CODEBUDDY_SESSION_ID") or os.environ.get("WORKBUDDY_SESSION_ID")
    if not sid:
        return None
    base = Path.home() / ".workbuddy" / "projects"
    try:
        for proj in base.iterdir():
            if proj.is_dir() and (proj / f"{sid}.jsonl").exists():
                return _decode_project_dir(proj.name)
    except Exception:
        pass
    return None


def _latest_workbuddy_project() -> Optional[Path]:
    """兜底：取 ~/.workbuddy/projects/ 下最近活跃的工作区目录反解。"""
    base = Path.home() / ".workbuddy" / "projects"
    try:
        candidates = [d for d in base.iterdir() if d.is_dir()]
    except Exception:
        return None
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for proj in candidates[:5]:
        decoded = _decode_project_dir(proj.name)
        if decoded:
            return decoded
    return None


def _detect_or_prompt_work_dir() -> Path:
    """输出根目录判定优先级（安装后零配置即可自动落到用户当前工作区）：
    1) 环境变量 MULTI_MEDIA_OUTPUT（最高优先，可 setx 永久指向指定目录）
    2) 用户级配置 ~/.workbuddy/multi-media-processor.conf（技能目录外，重装不丢）
    3) 技能目录内 .work_dir.conf（兼容旧版安装）
    4) 当前会话工作区：CODEBUDDY_SESSION_ID → ~/.workbuddy/projects 反解
    5) 进程工作目录（若像工作区；WorkBuddy 正常调用时通常即当前工作区）
    6) WorkBuddy 最近活跃项目（projects 目录 mtime 最新者）
    7) 回退：用户主目录/multi-media
    """
    user_config = Path.home() / ".workbuddy" / "multi-media-processor.conf"
    skill_config = SKILL_DIR / ".work_dir.conf"  # 兼容旧版

    env = os.environ.get("MULTI_MEDIA_OUTPUT")
    if env:
        result = Path(env).resolve()
        _save_work_dir(result, user_config, skill_config)
        return result

    for cfg in (user_config, skill_config):
        try:
            if cfg.exists():
                saved = cfg.read_text(encoding="utf-8").strip()
                if saved and Path(saved).is_dir():
                    return Path(saved)
        except Exception:
            pass

    # 无任何显式配置 -> 探测 WorkBuddy 工作区
    ws = _workspace_from_session() or (
        Path.cwd().resolve() if _is_plausible_workspace(Path.cwd()) else None
    ) or _latest_workbuddy_project()
    if ws:
        _save_work_dir(ws, user_config, skill_config)
        return ws

    default = Path.home() / "multi-media"
    _save_work_dir(default, user_config, skill_config)
    return default


def _default_output_root() -> Path:
    return _detect_or_prompt_work_dir() / "multi-media"

OUTPUT_ROOT = _default_output_root()
PY = sys.executable

# LLM 总开关：默认 False —— 全程不调用任何 LLM，纯本地规则式处理。
# 仅当用户显式要求时开启：命令行 `--llm`，或环境变量 LLM_ENABLED=1。
LLM_ENABLED = os.environ.get("LLM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TC2SC = {
    "個": "个", "輕": "轻", "結": "结", "親": "亲", "開": "开", "係": "系", "樣": "样",
    "東": "东", "題": "题", "課": "课", "關": "关", "實": "实", "幫": "帮", "點": "点",
    "態": "态", "將": "将", "來": "来", "過": "过", "質": "质", "時": "时", "還": "还",
    "從": "从", "們": "们", "這": "这", "那": "那", "麼": "么", "隻": "只", "裏": "里",
    "說": "说", "話": "话", "歲": "岁", "會": "会", "沒": "没", "給": "给", "讓": "让",
    "對": "对", "當": "当", "應": "应", "產": "产", "處": "处", "與": "与", "後": "后",
    "覺": "觉", "認": "认", "請": "请", "問": "问", "體": "体", "發": "发",
    "動": "动", "張": "张", "強": "强", "細": "细", "終": "终", "遠": "远", "書": "书",
    "樂": "乐", "愛": "爱", "買": "买", "賣": "卖", "讀": "读", "學": "学", "習": "习",
    "見": "见", "視": "视", "聽": "听", "願": "愿", "氣": "气", "長": "长", "門": "门",
    "間": "间", "顧": "顾", "養": "养", "醫": "医", "網": "网", "財": "财", "專": "专",
    "業": "业", "歷": "历", "經": "经", "驗": "验", "設": "设", "計": "计", "許": "许",
    "論": "论", "試": "试", "證": "证", "資": "资", "優": "优", "擇": "择", "擔": "担",
    "積": "积", "極": "极", "團": "团", "環": "环", "節": "节", "類": "类", "燈": "灯",
    "靈": "灵", "畫": "画", "尋": "寻", "導": "导", "則": "则", "創": "创", "辦": "办",
    "園": "园", "異": "异", "補": "补",
    # —— 补充常用繁体（覆盖 ASR/口语常见字，兜底 zhconv 不可用场景）——
    "為": "为", "於": "于", "國": "国", "裏": "里", "裡": "里", "萬": "万", "歲": "岁",
    "喫": "吃", "淚": "泪", "愛": "爱", "寶": "宝", "幫": "帮", "報": "报", "邊": "边",
    "變": "变", "別": "别", "賓": "宾", "參": "参", "層": "层", "場": "场", "車": "车",
    "稱": "称", "誠": "诚", "遲": "迟", "衝": "冲", "醜": "丑", "齣": "出", "傳": "传",
    "窗": "窗", "詞": "词", "從": "从", "錯": "错", "達": "达", "帶": "带", "單": "单",
    "彈": "弹", "導": "导", "島": "岛", "敵": "敌", "遞": "递", "電": "电", "調": "调",
    "頂": "顶", "丟": "丢", "東": "东", "凍": "冻", "獨": "独", "讀": "读", "斷": "断",
    "隊": "队", "對": "对", "頓": "顿", "兒": "儿", "爾": "尔", "飯": "饭", "範": "范",
    "費": "费", "奮": "奋", "風": "风", "豐": "丰", "婦": "妇", "復": "复", "該": "该",
    "幹": "干", "剛": "刚", "鋼": "钢", "個": "个", "給": "给", "夠": "够", "構": "构",
    "夠": "够", "顧": "顾", "掛": "挂", "關": "关", "廣": "广", "歸": "归", "貴": "贵",
    "過": "过", "還": "还", "韓": "韩", "漢": "汉", "號": "号", "喝": "喝", "護": "护",
    "畫": "画", "話": "话", "壞": "坏", "歡": "欢", "環": "环", "換": "换", "會": "会",
    "獲": "获", "機": "机", "雞": "鸡", "極": "极", "幾": "几", "記": "记", "際": "际",
    "濟": "济", "價": "价", "簡": "简", "見": "见", "件": "件", "健": "健", "腳": "脚",
    "較": "较", "節": "节", "結": "结", "解": "解", "緊": "紧", "進": "进", "經": "经",
    "靜": "静", "舊": "旧", "劇": "剧", "據": "据", "絕": "绝", "開": "开", "顆": "颗",
    "課": "课", "塊": "块", "來": "来", "藍": "蓝", "樂": "乐", "離": "离", "裏": "里",
    "禮": "礼", "歷": "历", "臉": "脸", "練": "练", "兩": "两", "輛": "辆", "瞭": "了",
    "領": "领", "劉": "刘", "龍": "龙", "樓": "楼", "錄": "录", "綠": "绿", "亂": "乱",
    "輪": "轮", "買": "买", "賣": "卖", "滿": "满", "麼": "么", "們": "们", "夢": "梦",
    "麵": "面", "秒": "秒", "滅": "灭", "鳴": "鸣", "麼": "么", "妳": "你", "難": "难",
    "腦": "脑", "妳": "你", "念": "念", "鳥": "鸟", "農": "农", "濃": "浓", "暖": "暖",
    "歐": "欧", "盤": "盘", "跑": "跑", "碰": "碰", "飄": "飘", "蘋": "苹", "齊": "齐",
    "騎": "骑", "簽": "签", "錢": "钱", "槍": "枪", "橋": "桥", "請": "请", "區": "区",
    "卻": "却", "確": "确", "讓": "让", "熱": "热", "認": "认", "賽": "赛", "掃": "扫",
    "殺": "杀", "閃": "闪", "傷": "伤", "燒": "烧", "設": "设", "誰": "谁", "聲": "声",
    "勝": "胜", "師": "师", "濕": "湿", "時": "时", "實": "实", "識": "识", "勢": "势",
    "試": "试", "視": "视", "輸": "输", "樹": "树", "雙": "双", "誰": "谁", "說": "说",
    "絲": "丝", "隨": "随", "歲": "岁", "臺": "台", "態": "态", "談": "谈", "湯": "汤",
    "題": "题", "體": "体", "條": "条", "聽": "听", "頭": "头", "圖": "图", "團": "团",
    "萬": "万", "網": "网", "為": "为", "問": "问", "無": "无", "係": "系", "細": "细",
    "嚇": "吓", "鮮": "鲜", "現": "现", "線": "线", "鄉": "乡", "想": "想", "響": "响",
    "項": "项", "寫": "写", "謝": "谢", "興": "兴", "許": "许", "選": "选", "學": "学",
    "樣": "样", "藥": "药", "爺": "爷", "業": "业", "頁": "页", "醫": "医", "異": "异",
    "銀": "银", "應": "应", "營": "营", "擁": "拥", "與": "与", "語": "语", "遠": "远",
    "願": "愿", "雲": "云", "運": "运", "雜": "杂", "載": "载", "贓": "赃", "張": "张",
    "找": "找", "這": "这", "針": "针", "爭": "争", "證": "证", "隻": "只", "紙": "纸",
    "種": "种", "眾": "众", "週": "周", "豬": "猪", "專": "专", "轉": "转", "裝": "装",
    "準": "准", "資": "资", "總": "总", "組": "组", "最": "最", "做": "做", "鐘": "钟",
    # —— 第二轮补充（异体/生僻繁体，进一步兜底）——
    "箇": "个", "測": "测", "數": "数", "術": "术", "講": "讲", "評": "评", "譯": "译",
    "護": "护", "讓": "让", "誤": "误", "調": "调", "談": "谈", "證": "证", "識": "识",
    "議": "议", "讀": "读", "該": "该", "說": "说", "誰": "谁", "諒": "谅", "諾": "诺",
    "課": "课", "論": "论", "詢": "询", "諷": "讽", "諾": "诺", "讚": "赞", "賤": "贱",
    "賬": "账", "購": "购", "販": "贩", "責": "责", "貴": "贵", "資": "资", "賭": "赌",
    "贈": "赠", "賓": "宾", "責": "责", "貢": "贡", "貨": "货", "貸": "贷", "費": "费",
    "賠": "赔", "贏": "赢", "賞": "赏", "賦": "赋", "賽": "赛", "趨": "趋", "趕": "赶",
    "軌": "轨", "轉": "转", "載": "载", "輯": "辑", "較": "较", "輪": "轮", "輸": "输",
    "辭": "辞", "辦": "办", "邊": "边", "還": "还", "這": "这", "遠": "远", "連": "连",
    "進": "进", "過": "过", "運": "运", "遊": "游", "達": "达", "違": "违", "遲": "迟",
    "選": "选", "遷": "迁", "遺": "遗", "避": "避", "邊": "边", "鄰": "邻", "郵": "邮",
    "鄉": "乡", "鄭": "郑", "醫": "医", "釋": "释", "針": "针", "釣": "钓", "鐘": "钟",
    "鋼": "钢", "錢": "钱", "鎖": "锁", "錯": "错", "鎮": "镇", "鏡": "镜", "鏈": "链",
    "鋒": "锋", "銷": "销", "鋪": "铺", "錄": "录", "鑑": "鉴", "閃": "闪", "閉": "闭",
    "開": "开", "關": "关", "門": "门", "聞": "闻", "閱": "阅", "閣": "阁", "隊": "队",
    "階": "阶", "陽": "阳", "陰": "阴", "陳": "陈", "隨": "随", "險": "险", "際": "际",
    "雙": "双", "雜": "杂", "離": "离", "難": "难", "電": "电", "雷": "雷", "霧": "雾",
    "類": "类", "顧": "顾", "頭": "头", "領": "领", "頻": "频", "顯": "显", "頂": "顶",
    "順": "顺", "須": "须", "項": "项", "願": "愿", "顧": "顾", "風": "风", "飛": "飞",
    "飯": "饭", "飲": "饮", "養": "养", "飽": "饱", "餅": "饼", "餃": "饺", "館": "馆",
    "馬": "马", "駕": "驾", "騎": "骑", "騙": "骗", "驗": "验", "驚": "惊", "麗": "丽",
    "黃": "黄", "龍": "龙", "龜": "龟", "點": "点", "黨": "党", "齊": "齐", "齒": "齿",
    "齡": "龄", "歲": "岁", "鐘": "钟", "鈔": "钞", "鋼": "钢", "錢": "钱", "銜": "衔",
    "鳴": "鸣", "鳥": "鸟", "鴨": "鸭", "雞": "鸡", "鵝": "鹅", "麥": "麦", "麪": "面",
}


# ==================== 依赖自动安装 ====================
INSTALL_SCRIPT = HERE.parent / "install_dependencies.py"


def _add_bundled_ffmpeg():
    ffmpeg_bin = WHISPER_DIR / "bin"
    if ffmpeg_bin.exists() and str(ffmpeg_bin) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", "")


def _load_installer():
    spec = importlib.util.spec_from_file_location("mmp_install_deps", str(INSTALL_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ensure_runtime_deps(needed_names: Optional[List[str]] = None):
    _add_bundled_ffmpeg()
    if needed_names is not None and not needed_names:
        return
    try:
        mod = _load_installer()
    except Exception as e:
        log(f"[依赖] 加载安装脚本失败: {e}")
        return
    try:
        deps = mod.DEPENDENCIES
        if needed_names is not None:
            deps = [d for d in deps if d["path"].name in needed_names]
        missing = [d for d in deps if not mod.check_dependency(d)]
        if not missing:
            return
        log(f"[依赖] 检测到 {len(missing)} 个大文件缺失，开始自动下载（仅此一次，约 3-4 分钟）...")
        ok = mod.ensure_deps([d["path"].name for d in missing])
        _add_bundled_ffmpeg()
        if ok:
            log("[依赖] 自动安装完成 ✅")
        else:
            log("[依赖] 部分依赖下载失败，相关功能可能不可用。")
    except Exception as e:
        log(f"[依赖] 自动安装异常: {e}")


# ==================== 配置 ====================
def _read_sph_cookie(cfg_path: Path) -> str:
    """从 config.yaml 读取 sphCookie 值（不引入 PyYAML，按行解析）"""
    try:
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*sphCookie\s*:\s*["\']?(.*?)["\']?\s*$', line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""


def _parse_cookie_string(cookie_str: str) -> dict:
    """解析 Cookie 字符串为字典

    支持格式：
    - sessionid=xxx; ttwid=yyy
    - sessionid=xxx\nttwid=yyy
    """
    cookies = {}
    # 支持分号或换行分隔
    for item in re.split(r'[;\n]+', cookie_str):
        item = item.strip()
        if '=' in item:
            key, value = item.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


def _is_cookie_input(text: str) -> bool:
    """检测输入是否为Cookie字符串（非URL、非文件路径）"""
    # 排除URL和文件路径
    if any(k in text.lower() for k in ["http://", "https://", "www.", ".com", ".cn"]):
        return False
    if "/" in text or "\\" in text:
        return False
    # 检查是否包含Cookie关键字
    cookie_keys = ["sessionid", "ttwid", "odin_tt", "sphCookie", "cookie"]
    return any(k in text.lower() for k in cookie_keys)


def _save_douyin_cookies(cookies: dict) -> Path:
    """保存抖音cookies到配置文件"""
    config_dir = Path.home() / ".config" / "multi-media-processor"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "cookies.txt"

    with open(config_path, 'w', encoding='utf-8') as f:
        f.write("# 抖音 Cookie 配置（由用户手动粘贴或浏览器自动获取）\n")
        f.write("# 有效期约1个月，过期后技能会提示重新配置\n\n")
        for key, value in cookies.items():
            f.write(f"{key}={value}\n")

    log(f"[配置] 抖音 Cookies 已保存到: {config_path}")
    return config_path


def _handle_cookie_input(input_str: str) -> bool:
    """处理用户粘贴的Cookie字符串"""
    if not _is_cookie_input(input_str):
        return False

    log(f"[Cookie] 检测到Cookie配置请求")

    # 解析Cookie字符串
    cookies_dict = _parse_cookie_string(input_str)

    if not cookies_dict:
        log("[Cookie] 未能解析Cookie，请检查格式")
        return True

    # 保存Cookie
    config_path = _save_douyin_cookies(cookies_dict)

    log(f"[Cookie] 配置完成，已保存 {len(cookies_dict)} 个Cookie")
    log("[Cookie] 提示：下次使用时会自动读取配置，无需再次粘贴")

    return True


def _ensure_proxy_disabled(cfg_path: Path) -> bool:
    """运行时安全网：把 config.yaml 的 proxy 段强制关闭。

    2026-08-26 事故复盘：工具默认 proxy.enabled+system=true 会把 Windows 系统
    代理指向 127.0.0.1:2023 —— Go 程序调 netsh 每次闪 CMD 黑窗；进程被强杀时
    代理设置残留导致全系统断网（ECONNREFUSED）。视频号解析走 sphCookie 方案
    无需系统代理。每次启动工具前自动检查并修正（安装器安装时亦会预置关闭，
    此为第二道防线）。仅改 enabled/system 两键，不触碰 sphCookie。"""
    if not cfg_path.exists():
        return False
    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return False
    in_proxy, changed = False, False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if line and not line[0].isspace() and stripped and not stripped.startswith("#"):
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
            cfg_path.write_text("".join(lines), encoding="utf-8")
            log("[安全网] 已自动关闭 config.yaml 的 proxy 段（防系统代理劫持/黑窗/断网）")
        except Exception as e:
            log(f"[安全网] 写回 config.yaml 失败: {e}")
    return changed


def ensure_config():
    tool_cfg = TOOL_DIR / "config.yaml"
    tool_cfg_tpl = TOOL_DIR / "config.template.yaml"
    if not tool_cfg.exists():
        if tool_cfg_tpl.exists():
            shutil.copy(tool_cfg_tpl, tool_cfg)
        else:
            log("[配置] 缺少 " + str(tool_cfg))
            return False
    # 运行时安全网：无论 Cookie 是否就绪，先确保 proxy 段关闭
    _ensure_proxy_disabled(tool_cfg)
    # 修复(2026-08-26): 原版仅当模板存在时才提示配置 Cookie；实际安装 zip 自带
    # config.yaml，导致 sphCookie 为空时不做任何提示、直接撞 parse_sph 400
    if _read_sph_cookie(tool_cfg):
        return True
    log("[配置] 配置文件: " + str(tool_cfg))
    log("请打开它，把 cloudflare.sphCookie 填为你的元宝 Cookie，保存后重跑。")
    log("获取方式（自动获取）：在浏览器登录 yuanbao.tencent.com 后重试")
    log("获取方式（手动粘贴）：F12 → Application → Cookies → 复制全部值拼成 k=v;k=v 字符串")
    return False


# ==================== 服务管理 ====================
def port_up(port=2022):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def start_tool():
    exe = TOOL_DIR / ("wx_video_download.exe" if os.name == "nt" else "wx_video_download")
    if not exe.exists():
        log(f"[tool] 错误: 未找到 {exe}")
        return False
    # 运行时安全网：启动工具前确保 proxy 关闭（防系统代理劫持/黑窗/断网）
    _ensure_proxy_disabled(TOOL_DIR / "config.yaml")
    log(f"[tool] 后台启动 {exe.name} ...")
    log_path = os.path.join(str(TOOL_DIR), "stdout.log")
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = 0x00000008
    with open(log_path, "ab") as lf:
        subprocess.Popen(
            [str(exe)], cwd=str(TOOL_DIR),
            stdout=lf, stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
    for i in range(30):
        time.sleep(1)
        if port_up():
            log(f"[tool] 已就绪（{i+1}s）")
            return True
    log("[tool] 启动超时")
    return False


def ensure_tool():
    if port_up():
        return True
    return start_tool()


# ==================== 链接类型检测 ====================
def is_sph(url):
    return "weixin.qq.com/sph" in url


def is_mp(url):
    return "mp.weixin.qq.com" in url


def sph_id(url):
    m = re.search(r"/sph/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else "video"


def is_local_file(path: str) -> bool:
    """判断是否为本地文件路径"""
    return os.path.isfile(path) and os.path.exists(path)

def detect_platform(url: str) -> str:
    u = url.lower()
    if "bilibili.com" in u or "b23.tv" in u or "bilibili.cn" in u:
        return "bilibili"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if any(k in u for k in ["weibo.com", "video.weibo.com", "m.weibo.cn"]):
        return "weibo"
    if any(k in u for k in ["twitter.com", "x.com"]):
        return "twitter"
    if any(k in u for k in ["vimeo.com"]):
        return "vimeo"
    if any(k in u for k in ["tiktok.com", "vm.tiktok.com"]):
        return "tiktok"
    if any(k in u for k in ["dailymotion.com", "dai.ly"]):
        return "dailymotion"
    if any(k in u for k in ["reddit.com", "v.redd.it"]):
        return "reddit"
    if any(k in u for k in ["instagram.com", "instagr.am"]):
        return "instagram"
    if any(k in u for k in ["xiaohongshu.com", "xhslink.com", "xhslink.cn"]):
        return "xiaohongshu"
    if any(k in u for k in ["douyin.com", "iesdouyin.com"]):
        return "douyin"
    return "unknown"


def resolve_b23(url: str) -> str:
    """跟随 b23.tv 短链重定向，返回最终 URL（含 BV 号）。

    修复(2026-08-27): 原版 extract_id 对 b23.tv 短链不处理，只匹配 BV1/av，
    导致 b23 链接走兜底 safe_slug(url)，vid 变成整条 URL 的安全化串而非视频 ID。
    新增此函数在 detect_platform/extract_id 之前解析短链。"""
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15, headers={"User-Agent": UA})
        if r.status_code < 400:
            return str(r.url)
    except Exception as e:
        log(f"[b23] 短链解析失败: {e}")
    return url


def extract_id(url: str, platform: str) -> str:
    if platform == "youtube":
        m = re.search(r"v=([\w-]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"youtu\.be/([\w-]+)", url)
        if m:
            return m.group(1)
    elif platform == "bilibili":
        # 修复(2026-08-27): 支持 b23.tv 短链——先解析为完整 URL 再提取 BV 号
        if "b23.tv" in url.lower():
            url = resolve_b23(url)
        m = re.search(r"BV1(\w+)", url)
        if m:
            return "BV1" + m.group(1)
        m = re.search(r"av(\d+)", url, re.I)
        if m:
            return m.group(1)
        # b23.tv 短码兜底（解析失败时提取路径片段）
        m = re.search(r"b23\.tv/([A-Za-z0-9]+)", url, re.I)
        if m:
            return m.group(1)
    elif platform == "vimeo":
        m = re.search(r"vimeo\.com/(\d+)", url)
        if m:
            return m.group(1)
    elif platform == "weibo":
        m = re.search(r"fid=1034[:;](\d+)", url)
        if m:
            return m.group(1)
    elif platform == "tiktok":
        m = re.search(r"video/(\d+)", url)
        if m:
            return m.group(1)
    elif platform == "dailymotion":
        m = re.search(r"dailymotion\.com/video/(\w+)", url)
        if m:
            return m.group(1)
        m = re.search(r"dai\.ly/(\w+)", url)
        if m:
            return m.group(1)
    elif platform == "twitter":
        m = re.search(r"status/(\d+)", url)
        if m:
            return m.group(1)
    elif platform == "reddit":
        m = re.search(r"reddit\.com/r/\w+/comments/(\w+)/", url)
        if m:
            return m.group(1)
    elif platform == "instagram":
        m = re.search(r"p/(\w+)|reel/(\w+)", url)
        if m:
            return m.group(1) or m.group(2)
    elif platform == "xiaohongshu":
        m = re.search(r"discovery/item/([a-f0-9]+)", url)
        if m:
            return m.group(1)
    elif platform == "douyin":
        # 抖音长链: /video/7xxxxxxxxxxx
        m = re.search(r"video/(\d+)", url)
        if m:
            return m.group(1)
        # 抖音短链/其他格式: 提取10+字符的ID片段
        m = re.search(r"([\w-]{10,})", url)
        if m:
            return m.group(1)
    # 兜底：未匹配到干净 ID 时，返回 URL 的安全片段（避免短链整条 URL 成为非法文件名）
    return safe_slug(url)


# ==================== 微信视频号 ====================
def process_sph(url, outdir):
    ensure_tool()
    log("\n=== 1. parse_sph 解析原地址 ===")
    r = httpx.get(PARSE_SPH, params={"url": url}, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        # 修复(2026-08-26): 原 assert 直接抛原始 JSON；Cookie 类错误给出明确指引
        msg = str(data)
        if "cookie" in msg.lower():
            log(f"[解析失败] {msg[:300]}")
            sys.exit("\nCookie 缺失或已失效：请更新 bin/wx_channels_download/config.yaml 的 "
                     "cloudflare.sphCookie（元宝 Cookie 会定期过期，重新按 F12 抓取即可）。")
        raise RuntimeError(f"解析失败: {data}")
    feed = data["data"]["data"]["feedInfo"]
    author = data["data"]["data"]["authorInfo"]["nickname"]
    desc = feed.get("description", "")
    video_url = feed["h264VideoInfo"]["videoUrl"]
    log(f"作者: {author}")
    log(f"描述: {desc}")
    log(f"videoUrl: {video_url[:90]}...")

    mp4 = os.path.join(outdir, "video.mp4")
    log("\n=== 2. 下载视频 ===")
    with httpx.stream("GET", video_url, timeout=180, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(mp4, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
    log(f"视频已存: {mp4}  ({os.path.getsize(mp4)} bytes)")

    log("\n=== 3. Whisper 本地转写 (small, zh) ===")
    transcript = os.path.join(outdir, "transcript_raw.txt")
    work = os.path.join(str(WHISPER_WORK), sph_id(url))
    try:
        _silent_check_call([
            PY, str(WHISPER_DIR / "scripts" / "transcribe.py"), mp4,
            "--model", "small", "--lang", "zh",
            "--out", transcript, "--exe", str(WHISPER_CLI),
            "--work", work,
        ], timeout=1800)
    except subprocess.CalledProcessError as e:
        log(f"[Whisper] 转写失败 (exit={e.returncode})，跳过转写步骤")
    except subprocess.TimeoutExpired:
        log("[Whisper] 转写超时（>30分钟），已终止进程")
    else:
        with open(transcript, encoding="utf-8") as f:
            raw = f.read()
        simp = "".join(TC2SC.get(ch, ch) for ch in raw)
        if simp != raw:
            transcript_sc = os.path.join(outdir, "transcript_simplified.txt")
            with open(transcript_sc, "w", encoding="utf-8") as f:
                f.write(simp)
            log(f"已生成简体副本: {transcript_sc}")
        log(f"转写已存: {transcript}  ({os.path.getsize(transcript)} bytes)")
        # 生成带时间戳的 TXT
        _srt = os.path.join(outdir, "subtitle.srt")
        if os.path.exists(_srt):
            try:
                # 视频号用描述/作者作为命名主题（无描述时回退 video）
                _sph_title = safe_slug(desc) or safe_slug(author) or "video"
                generate_timestamped_txt(outdir, _sph_title)
            except Exception as e:
                log(f"[时间戳TXT] 生成失败: {e}")

    meta = {
        "type": "sph_video", "url": url, "author": author,
        "description": desc, "video_url": video_url,
        "files": {"video": mp4, "transcript": transcript},
    }
    with open(os.path.join(outdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# ==================== 微信公众号 ====================
def _strip_tags(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html)
    html = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", html, re.I)
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_encoding(enc: Optional[str]) -> str:
    """校验编码名是否可用，不可用则回退 utf-8。

    部分站点（含某些公众号页面）Content-Type 会返回畸形 charset，
    如 `text/html; charset=utf-8, text/html`，httpx 解析出 "utf-8, text/html"
    赋给 encoding 后会让 r.text 抛 LookupError。"""
    if not enc:
        return "utf-8"
    try:
        import codecs
        codecs.lookup(enc)
        return enc
    except (LookupError, ValueError, TypeError):
        return "utf-8"


def _response_text(r) -> str:
    """安全取响应正文：编码异常时逐级回退，绝不因 Content-Type 畸形而崩溃。"""
    enc = _safe_encoding(getattr(r, "charset_encoding", None))
    try:
        return r.content.decode(enc, errors="replace")
    except Exception:
        try:
            return r.content.decode("utf-8", errors="replace")
        except Exception:
            return r.content.decode("utf-8", errors="ignore")


def process_mp(url, outdir):
    log("\n=== 公众号文章抓取 ===")
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
    html = _response_text(r)

    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    title = m.group(1).strip() if m else ""
    m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    summary = m.group(1).strip() if m else ""

    m = re.search(r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    body = _strip_tags(m.group(1)) if m else ""

    md = f"# {title}\n\n> 来源：{url}\n\n"
    if summary:
        md += f"> 摘要：{summary}\n\n"
    md += (body or "（正文提取为空，可能文章需登录或模板异常）")
    article = os.path.join(outdir, "article.md")
    with open(article, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"标题: {title}")
    log(f"正文长度: {len(body)} 字")
    log(f"文章已存: {article}")

    meta = {"type": "mp_article", "url": url, "title": title,
            "summary": summary, "files": {"article": article}}
    with open(os.path.join(outdir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta




# ==================== 抖音下载（需要cookies）====================
def download_douyin(url: str, vid: str, outdir: Path) -> Path:
    """
    抖音专用下载器：通过 aweme API 获取视频信息并下载。
    需要先配置 cookies（通过配置文件或会话粘贴）。
    
    Cookie配置方式：
    1. 自动获取：在浏览器登录抖音后，技能尝试从浏览器读取Cookie
    2. 手动粘贴：将Cookie字符串粘贴到会话窗口，格式如：sessionid=xxx; ttwid=xxx
    """
    import urllib.parse as urlparse
    
    # 尝试从配置文件读取cookies
    cookies_dict = {}
    config_path = Path.home() / ".config" / "multi-media-processor" / "cookies.txt"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    cookies_dict[key] = value
    
    if not cookies_dict:
        raise ValueError(
            "抖音下载需要配置cookies。\n\n"
            "配置方式1（自动获取）：请在浏览器登录抖音网页版，技能会自动读取Cookie\n"
            "配置方式2（手动粘贴）：请提供Cookie字符串，格式如：sessionid=xxx; ttwid=xxx\n\n"
            "Cookie获取步骤：\n"
            "1. 打开浏览器访问 https://www.douyin.com 并登录\n"
            "2. 按F12打开开发者工具 → Application → Cookies → .douyin.com\n"
            "3. 复制 sessionid、ttwid、odin_tt 等关键Cookie值\n"
            "4. 粘贴到会话窗口或保存至配置文件"
        )
    
    # 解析视频ID - 支持长链和短链
    # 先尝试直接提取
    m = re.search(r"video/(\d+)", url)
    if m:
        video_id = m.group(1)
    else:
        # 短链情况：需要先跟随重定向获取真实URL
        try:
            r = httpx.get(url, follow_redirects=True, timeout=10, 
                         headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code < 400:
                m = re.search(r"video/(\d+)", str(r.url))
                if m:
                    video_id = m.group(1)
                else:
                    video_id = vid  # 使用传入的vid作为备选
            else:
                video_id = vid
        except Exception:
            video_id = vid
    
    # 调用API获取视频信息
    api_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    params = {
        "aweme_id": video_id,
        "aid": 1128,
        "cookie_enabled": "true",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://www.douyin.com/video/{video_id}",
    }
    
    log(f"[抖音] 正在获取视频信息...")
    r = httpx.get(api_url, params=params, headers=headers, cookies=cookies_dict, timeout=15)
    
    if r.status_code != 200:
        raise ValueError(f"API请求失败: {r.status_code}")
    
    data = r.json()
    if "aweme_detail" not in data or data["aweme_detail"] is None:
        raise ValueError(f"API返回数据异常: {data}")
    
    aweme = data["aweme_detail"]
    _raw_desc = aweme.get("desc", f"抖音视频_{video_id}") or f"抖音视频_{video_id}"
    # 清洗标题：去除 #话题标签 与 末尾视频 ID（抖音 desc 常携带），保证产物命名干净
    title = _clean_video_title(_raw_desc) or _raw_desc
    author = aweme.get("author", {}).get("nickname", "未知作者")
    
    log(f"视频标题: {title}")
    log(f"作者: {author}")
    
    # 获取视频URL
    video = aweme.get("video", {})
    play_addr = video.get("play_addr", {})
    url_list = play_addr.get("url_list", [])
    
    if not url_list:
        raise ValueError("未找到视频下载地址")
    
    download_url = url_list[0]
    
    # 下载视频
    target = outdir / f"douyin_{safe_slug(title)[:30]}_{video_id}.mp4"
    log(f"[抖音] 正在下载: {target.name}")
    
    with httpx.stream("GET", download_url, headers=headers, cookies=cookies_dict, timeout=300, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(target, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
    
    sz = target.stat().st_size
    log(f"[抖音] 完成: {sz / 1024 / 1024:.1f} MB → {target}")
    return target


# ==================== 多平台视频下载 ====================
def download_xiaohongshu(url: str, vid: str, outdir: Path) -> Path:
    target = outdir / f"xhs_{vid}.mp4"
    log(f"[小红书] 使用 yt-dlp 下载: {url}")
    try:
        ydl_opts = {
            "format": "best",
            "merge_output_format": "mp4",
            "outtmpl": str(target),
            "noplaylist": True,
            "quiet": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        sz = target.stat().st_size
        log(f"[小红书] 完成: {sz / 1024 / 1024:.1f} MB → {target}")
        return target
    except Exception as e:
        raise ValueError(f"小红书下载失败: {e}")


def download_ytdlp(url: str, platform: str, vid: str, outdir: Path) -> Path:
    # 优先用解析出的内容标题作为文件名（用户建议）：避免短链 URL 含非法字符导致 WinError 123，
    # 同时让产物文件名更具可读性。标题获取失败时回退到安全化的 vid。
    title = ""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as _ydl:
            _info = _ydl.extract_info(url, download=False)
            if isinstance(_info, dict):
                title = _info.get("title") or ""
    except Exception:
        title = ""
    name_base = safe_slug(title) or safe_slug(vid) or safe_slug(platform)
    target = outdir / f"{platform}_{name_base}.mp4"
    fmt = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"
    if platform == "bilibili":
        fmt = "bv*[height<=720]+ba[ext=m4a]/b[height<=720]"
    if platform == "dailymotion":
        fmt = "hls"
    if platform in ("twitter", "reddit", "instagram"):
        fmt = "best"
    if platform == "vimeo":
        fmt = None
    if platform == "youtube":
        fmt = "134+140/136+140/137+140/best[height<=720]"

    if platform == "vimeo":
        return download_vimeo_ffmpeg(url, vid, outdir)

    ydl_opts = {
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(target),
        "noplaylist": True,
    }
    log(f"[yt-dlp] {platform} → {target}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    sz = target.stat().st_size
    log(f"[yt-dlp] 完成: {sz / 1024 / 1024:.1f} MB")
    return target


def download_vimeo_ffmpeg(url: str, vid: str, outdir: Path) -> Path:
    target = outdir / f"vimeo_{vid}.mp4"
    log(f"[Vimeo] 使用 ffmpeg 方式下载: {url}")

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://player.vimeo.com/video/{vid}", download=False)
            formats = info.get('formats', [])

            target_format = None
            for f in formats:
                if f.get('format_id') == 'hls-fastly_skyfire-633':
                    target_format = f
                    break

            if not target_format:
                for f in formats:
                    if f.get('protocol') == 'hls' and f.get('ext') == 'm3u8':
                        target_format = f
                        break

            if not target_format:
                raise ValueError("未找到可用的 Vimeo 格式")

            m3u8_url = target_format['url']
            log(f"[Vimeo] 获取到 m3u8: {m3u8_url[:60]}...")

            cmd = [
                'ffmpeg', '-y', '-i', m3u8_url,
                '-c', 'copy', str(target)
            ]
            result = _silent_run(cmd, timeout=300)

            if os.path.exists(str(target)):
                size_mb = os.path.getsize(str(target)) / 1024 / 1024
                log(f"[Vimeo] 完成: {size_mb:.1f} MB → {target}")
                return target
            else:
                raise RuntimeError(f"ffmpeg 失败: {result.stderr[:500]}")

    except Exception as e:
        raise ValueError(f"Vimeo 下载失败: {e}")


def transcribe_whisper(video_path: Path, outdir: Path, model: str = "small", lang: str = "zh", denoise: bool = False, title: str = "transcript") -> str:
    log(f"[Whisper] 转写中 ({model}, {lang})...")
    work = WHISPER_WORK / video_path.stem
    # raw.txt 按主题命名（避免与 {title}_raw.txt 重复）；
    # subtitle.srt 保持固定名，generate_timestamped_txt 依赖它
    transcript = outdir / f"{safe_slug(title) or 'transcript'}_raw.txt"
    srt_path = outdir / "subtitle.srt"
    try:
        cmd = [
            PY, str(WHISPER_DIR / "scripts" / "transcribe.py"),
            str(video_path),
            "--model", model, "--lang", lang,
            "--out", str(transcript),
            "--out-srt", str(srt_path),
            "--exe", str(WHISPER_CLI),
            "--work", str(work),
        ]
        if denoise:
            cmd.append("--denoise")
        _silent_check_call(cmd, timeout=600)
    except subprocess.CalledProcessError as e:
        log(f"[Whisper] 转写失败: {e}")
        return ""
    if transcript.exists():
        text = transcript.read_text(encoding="utf-8")
        # 繁体转简体（三级兜底，杜绝残留繁体）
        text = _to_simplified(text)
        log(f"[Whisper] 完成: {len(text)} 字")
        if srt_path.exists():
            log(f"[字幕] SRT已生成: {srt_path}")
        return text.strip()
    return ""


def _to_simplified(text: str) -> str:
    """繁体转简体，三级兜底：zhconv → opencc → 内置 TC2SC 映射。
    保证任何运行环境下繁体都被转成简体，杜绝 DOCX 等产物残留繁体字。"""
    try:
        import zhconv
        return zhconv.convert(text, 'zh-cn')
    except ImportError:
        pass
    try:
        from opencc import OpenCC
        return OpenCC('t2s').convert(text)
    except ImportError:
        pass
    return "".join(TC2SC.get(ch, ch) for ch in text)


def _load_llm_config() -> Optional[Dict]:
    """读取 LLM 整理配置。优先级：环境变量 > 配置文件 ~/.config/multi-media-processor/llm.json。
    支持任意 OpenAI 兼容端点（含中转站）。

    **默认全程不启用 LLM**：只有用户显式要求时才工作——
    需通过 `--llm` 命令行参数，或环境变量 `LLM_ENABLED=1` 开启。
    返回 None 表示不启用，全部走本地规则式处理。"""
    if not LLM_ENABLED:      # 默认 False，用户显式开启后才读取配置
        return None
    cfg = {
        "api_key": (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
                    or os.environ.get("BIGMODEL_API_KEY") or os.environ.get("ZHIPU_API_KEY")
                    or os.environ.get("DEEPSEEK_API_KEY") or "").strip(),
        "base_url": (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                     or "").strip(),
        "model": (os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL")
                  or "").strip(),
    }
    if not cfg["api_key"]:
        cfg_path = Path.home() / ".config" / "multi-media-processor" / "llm.json"
        try:
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg["api_key"] = (data.get("api_key") or data.get("key") or "").strip()
                cfg["base_url"] = (data.get("base_url") or data.get("api_base") or "").strip()
                cfg["model"] = (data.get("model") or "").strip()
        except Exception:
            pass
    if not cfg["api_key"]:
        return None
    if not cfg["model"]:
        cfg["model"] = "gpt-4o-mini"
    return cfg


def llm_segment_and_punctuate(raw_text: str) -> Optional[str]:
    """调用 LLM 对 ASR 原文做智能整理：去填充词、繁转简、补标点、自然分段。
    成功返回整理后的文本（段落间空行分隔），失败/未配置返回 None 由调用方降级。"""
    cfg = _load_llm_config()
    if not cfg:
        return None
    try:
        import httpx
        base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        prompt = (
            "你是中文语音转写文本整理专家。请对下面的语音识别（ASR）原文进行整理：\n"
            "1. 去除口语化填充词（如：呃、啊、嗯、那个、就是说、就是、然后呢等）；\n"
            "2. 把所有繁体字转成简体字；\n"
            "3. 补全正确的标点符号（句号、逗号、问号、感叹号、引号等），让句子通顺自然；\n"
            "4. 按语义把内容分成若干自然段，段落之间用空行分隔；\n"
            "5. 不要增删、改写或总结原意，只做格式整理、断句和标点补全；\n"
            "6. 直接输出整理后的文本，不要任何解释、前缀或代码块标记。\n\n"
            "原文如下：\n" + raw_text
        )
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        r = httpx.post(url, json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if content:
            # 保险：LLM 输出再走一次本地繁转简，杜绝模型偶发繁体
            return _to_simplified(content)
    except Exception as e:
        log(f"[LLM整理] 调用失败，回退规则式处理: {e}")
    return None


def llm_script(raw_text: str) -> Optional[str]:
    """调用 LLM 把 ASR 原文整理成"可读脚本"：分章节（## 小标题）+ 段落 + 完整标点。
    返回 Markdown 文本（含 ## 章节标题），失败/未配置返回 None。"""
    cfg = _load_llm_config()
    if not cfg or len(raw_text) < 30:
        return None
    try:
        import httpx
        base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = base + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        prompt = (
            "你是中文视频脚本整理专家。请把下面这段语音识别（ASR）原文，"
            "整理成一份**结构清晰、便于阅读的完整脚本**。\n\n"
            "要求：\n"
            "1. 按内容发展/场景变化划分若干章节，每个章节用 Markdown 二级标题 `## ` 开头，"
            "标题要概括该章节内容（8 字以内，如「饭桌上的争执」「深夜的电话」）；\n"
            "2. 每个章节内部分成若干自然段，段落之间用空行分隔；\n"
            "3. 补全完整的标点符号（句号、逗号、问号、感叹号、引号、省略号），让句子通顺；\n"
            "4. 去除口语化填充词（呃、啊、嗯、那个、就是说、然后呢等），但保留语气词；\n"
            "5. 把所有繁体字转为简体字；\n"
            "6. 不要删减、改写或总结原意，只做结构整理、断句和标点补全；\n"
            "7. 直接输出 Markdown 正文（从第一个 `## ` 章节标题开始），"
            "不要任何解释、前言、总结或代码块标记。\n\n"
            "ASR 原文如下：\n" + raw_text
        )
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        r = httpx.post(url, json=payload, headers=headers, timeout=240)
        r.raise_for_status()
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if content and "##" in content:
            return _to_simplified(content)
    except Exception as e:
        log(f"[LLM脚本] 调用失败，回退规则式分章: {e}")
    return None


def _clean_video_title(title: str, maxlen: int = 30) -> str:
    """清洗视频标题用于命名：去掉 #话题标签、末尾视频 ID、多余符号，并限制长度。

    抖音等平台标题常形如：
    「还30年房贷和付30年房租，究竟哪个更划算？ #经济学 #房价 #租房 7674922960792063284」
    直接用会让文件名又长又含无意义 ID，这里清洗成可读的短标题。
    """
    if not title:
        return ""
    t = re.sub(r"#\S+", "", title)              # 去话题标签 #xxx
    t = re.sub(r"[_\s]*\d{10,}\s*$", "", t)     # 去末尾长数字 ID（≥10 位）
    t = re.sub(r"\s+", " ", t).strip(" _-·")
    if len(t) > maxlen:
        t = t[:maxlen].rstrip("，,、。！？· ")
    return t


def _is_meaningful_title(title: str) -> bool:
    """判断标题是否有实际语义（用于决定是否需要从转写内容重新提炼主题）。
    先剥离平台前缀（douyin_/bilibili_ 等），再识别默认占位名。"""
    if not title:
        return False
    t = title.strip()
    if not t:
        return False

    # 1) 剥离平台前缀（文件名形如 douyin_xxx.mp4，去前缀后才是内容标题）
    plat_prefixes = ('bilibili_', 'douyin_', 'weibo_', 'xiaohongshu_', 'youtube_',
                     'tiktok_', 'vimeo_', 'dailymotion_', 'twitter_')
    for p in plat_prefixes:
        if t.startswith(p):
            t = t[len(p):]
            break
    if not t:
        return False

    # 2) 纯数字 / 纯 ID / 纯下划线
    if re.fullmatch(r'[\d_]+', t):
        return False

    # 3) 默认占位名（剥离前缀后判断，覆盖"抖音视频_7678..."这类）
    bad = ('transcript', 'video', 'audio', '抖音视频', '视频', '工作', '音频',
           '无标题', '未命名')
    for b in bad:
        if t == b or t.startswith(b + '_') or t.startswith(b):
            return False

    return len(t) >= 2


def _derive_title(text: str, fallback: str = "transcript") -> str:
    """从转写内容提取一个契合主题的短标题（无 LLM 时的规则式兜底）。
    优先用 LLM 提取；无 LLM 则按高频实体词/关键场景词组合生成。"""
    cfg = _load_llm_config()
    if cfg:
        try:
            import httpx
            base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
            url = base + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            }
            prompt = (
                "请阅读下面的视频转写文字，提炼一个精炼的中文标题（10 字以内），"
                "能概括这段内容的主题。只输出标题本身，不要引号、解释或标点。\n\n"
                "转写文字：\n" + text[:1500]
            )
            payload = {
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            }
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            title = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            title = re.sub(r'[\'"“”《》【】\s]+', '', title)
            if 2 <= len(title) <= 20:
                return safe_slug(title) or fallback
        except Exception:
            pass

    # 规则式兜底：优先 jieba 分词统计高频实义词，无 jieba 时用 n-gram 滑窗
    stop = {'就是', '那个', '什么', '怎么', '不是', '我们', '你们', '他们', '自己',
            '一个', '这个', '这样', '那样', '没有', '知道', '可以', '还是', '因为',
            '所以', '但是', '如果', '然后', '时候', '现在', '已经', '还是', '或者',
            '觉得', '看到', '打过', '电话', '今天', '今晚', '真的', '可能', '叔叔',
            '爸爸', '妈妈', '阿姨', '老师', '老板', '女儿', '孩子', '回来', '快点'}

    # 用 jieba 词性标注，优先取实体名词（人名 nr / 地名 ns / 机构 nt / 专名 nz / 名词 n）
    try:
        import logging
        import jieba
        import jieba.posseg as pseg
        jieba.setLogLevel(logging.ERROR)  # 抑制 jieba 加载日志，避免污染输出

        entity: Dict[str, int] = {}
        common: Dict[str, int] = {}
        for w, flag in pseg.cut(text):
            w = w.strip()
            if len(w) < 2 or not re.fullmatch(r'[\u4e00-\u9fff]{2,6}', w):
                continue
            if w in stop:
                continue
            if flag in ('nr', 'ns', 'nt', 'nz'):      # 人名/地名/机构名/专有名词 → 最优先
                entity[w] = entity.get(w, 0) + 1
            elif flag.startswith('n'):                 # 普通名词 → 次优先
                common[w] = common.get(w, 0) + 1

        # 实体词优先：取高频实体（≥2 次，若不足则降为 1 次）
        for pool, min_cnt in ((entity, 2), (entity, 1), (common, 2), (common, 1)):
            picked = [w for w, c in sorted(pool.items(), key=lambda kv: -kv[1]) if c >= min_cnt][:2]
            if picked:
                title = "·".join(picked)
                return safe_slug(title)[:20] or fallback
    except ImportError:
        pass

    # 无 jieba：2-4 字 n-gram 滑窗兜底
    freq: Dict[str, int] = {}
    for n in (4, 3, 2):
        for i in range(len(text) - n + 1):
            w = text[i:i + n]
            if re.fullmatch(r'[\u4e00-\u9fff]+', w) and w not in stop:
                freq[w] = freq.get(w, 0) + 1
    picked = [w for w, c in sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]))) if c >= 2][:2]
    if picked:
        title = "·".join(picked)
        return safe_slug(title)[:20] or fallback
    return safe_slug(fallback) or "transcript"


def clean_transcript(text: str) -> str:
    """文本清洗：去除口语化填充词、修正重复、优化分段、补标点。

    - 繁体转简体（zhconv → opencc → TC2SC 三级兜底）
    - 优先调用 LLM 做智能断句/补标点/自然分段；无 LLM 配置时回退规则式
    - 规则式兜底：去除填充词、按语义句子分段、补全基础标点
    """
    import re

    # 繁体转简体（三级兜底，杜绝残留繁体）
    text = _to_simplified(text)

    # 优先 LLM 智能整理（断句 + 标点 + 分段），失败则回退规则式
    if len(text) >= 30:
        llm_result = llm_segment_and_punctuate(text)
        if llm_result:
            return llm_result.strip()

    # 填充词模式（口语化）
    filler_patterns = [
        r'呃[，,。]?',
        r'啊[，,。]?',
        r'嗯[，,。]?',
        r'那个[，,。]?',
        r'就是说[，,。]?',
        r'就是[，,。]?',
        r'嗯哼[，,。]?',
        r'然后呢[，,。]?',
        r'对对[，,。]?',
    ]

    cleaned = text
    for pattern in filler_patterns:
        cleaned = re.sub(pattern, '', cleaned)

    # 去除连续空白
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
    if not lines:
        return ''

    # 句子结束标记词
    sentence_enders_q = ['吗', '么', '哪', '谁', '什么', '怎么', '为什么', '何']
    sentence_enders_e = ['啊', '呀', '哇', '哦', '呵', '吧']
    speaker_patterns = [
        r'^(爸|妈|叔|姨|哥|姐|弟|妹|爹|娘|叔叔|阿姨|爸爸|妈妈)\s',
        r'^(喂|你好|请问|打扰|不好意思)\s',
    ]

    sentences = []
    current = ""

    for line in lines:
        if not line:
            continue
        current = (current + line).strip() if current else line

        # 判断当前 chunk 是否是一个完整句子
        is_sentence_end = False
        stripped = current.rstrip()

        if stripped:
            end_char = stripped[-1]
            if end_char in sentence_enders_q:
                is_sentence_end = True
            elif end_char in sentence_enders_e and len(stripped) > 1:
                is_sentence_end = True
            elif end_char in '。！？.?!':
                is_sentence_end = True

        # 达到一定长度且遇到说话人切换
        if len(current) >= 12 and not is_sentence_end:
            for sp in speaker_patterns:
                if re.match(sp, current):
                    is_sentence_end = True
                    break

        # 超长自动截断（超过30字强制结束）
        if len(current) >= 30:
            is_sentence_end = True

        if is_sentence_end:
            sentences.append(current.strip())
            current = ""

    if current:
        sentences.append(current.strip())

    # 合并过短的零散句子到上一句
    merged = []
    for s in sentences:
        if merged and len(s) < 6 and not any(c in s for c in '，。！？;:'):
            merged[-1] += s
        else:
            merged.append(s)
    sentences = merged

    # 为每个句子添加标点
    punctuated = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        last = s.rstrip()[-1] if s.rstrip() else ''
        if last in '。！？.?!':
            punctuated.append(s)
        elif last in sentence_enders_q:
            punctuated.append(s + '？')
        elif last in sentence_enders_e:
            punctuated.append(s + '！')
        else:
            # 无标记的句子：根据内容判断
            if any(w in s for w in ['难道', '真的', '什么', '怎么', '为什么', '吗', '么']):
                punctuated.append(s + '？')
            else:
                punctuated.append(s + '。')

    # 段落合并：每3-5个句子一段
    paragraphs = []
    para_buf = []
    for s in punctuated:
        para_buf.append(s)
        if len(para_buf) >= 4 or len(s) > 25:
            paragraphs.append(' '.join(para_buf))
            para_buf = []
    if para_buf:
        paragraphs.append(' '.join(para_buf))

    return '\n\n'.join(p for p in paragraphs if p.strip())


def _load_srt_segments(outdir: Path) -> List[tuple]:
    """解析 subtitle.srt，返回 [(start_ms, end_ms, text), ...]"""
    srt_path = outdir / "subtitle.srt"
    if not srt_path.exists():
        return []
    try:
        content = srt_path.read_text(encoding="utf-8")
    except Exception:
        return []
    ts_pattern = re.compile(r'(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})')
    segments, lines, i = [], content.split("\n"), 0
    while i < len(lines):
        m = ts_pattern.match(lines[i].strip())
        if m:
            sh, sm, ss, sms = (int(m.group(k)) for k in (1, 2, 3, 4))
            eh, em, es, ems = (int(m.group(k)) for k in (5, 6, 7, 8))
            start = sh * 3600000 + sm * 60000 + ss * 1000 + sms
            end = eh * 3600000 + em * 60000 + es * 1000 + ems
            texts, i = [], i + 1
            while i < len(lines) and lines[i].strip():
                texts.append(lines[i].strip())
                i += 1
            if texts:
                # SRT 内多行用逗号连接（比空格更接近自然语句）
                segments.append((start, end, "，".join(texts)))
        else:
            i += 1
    return segments


# ---- 语境标点规则库（纯本地规则，不依赖 LLM）----
# 称呼语：句首出现时其后补逗号
_VOCATIVE = (
    "妈妈", "爸爸", "母亲", "父亲", "妈", "爸", "叔叔", "大叔", "叔", "阿姨", "姨",
    "哥哥", "姐姐", "哥", "姐", "弟弟", "妹妹", "弟", "妹", "爷爷", "奶奶",
    "外公", "外婆", "姥姥", "姥爷", "老板娘", "老板", "先生", "女士", "小姐",
    "老师", "同学", "孩子", "女儿", "儿子", "宝贝", "姑娘", "小子",
    "乔总", "乔董", "乔先生", "乔英和", "乔明川", "郭宇宁", "玉宁", "云宁", "姜总",
)
# 句首祈使/命令词 → 感叹号
_IMPERATIVE = (
    "别再", "别动", "别吃", "别夹", "别挂", "别乱", "别哭", "别叫", "别去", "别走",
    "千万别", "别", "不要", "不许", "不准", "不准", "快点", "快去", "快", "赶紧",
    "赶快", "让我", "给我", "走开", "住手", "停下", "闭嘴", "等着", "小心", "注意",
    "一定", "必须", "马上",
)
# 疑问句：结尾字
_Q_END_CHARS = ("吗", "呢", "么", "嘛")
# 疑问句：结尾词
_Q_END_WORDS = (
    "什么", "为什么", "干什么", "干吗", "干嘛", "怎么", "怎样", "怎么办",
    "是不是", "有没有", "能不能", "要不要", "会不会", "对不对", "行不行",
    "好不好", "可以吗", "多少", "几岁", "几个", "哪里", "哪儿", "哪位",
    "是谁", "什么人", "什么事", "真的吗", "是吗",
)
# 疑问句：句首疑问副词
_Q_START_WORDS = ("怎么", "为什么", "什么", "哪里", "哪儿", "哪位", "谁", "难道", "为何", "咋")
# 感叹句：结尾字
_E_END_CHARS = ("啊", "呀", "哇", "啦", "噢")
_CN_NUM = "一二三四五六七八九十百千万零两"


def _fix_enumerate(s: str) -> str:
    """把 '一 二 三 寻找女儿' 这类列举改为顿号：'一、二、三，寻找女儿'"""
    m = re.match(rf'^((?:[{_CN_NUM}\d]\s+){{2,}}[{_CN_NUM}\d])(\s*)(.*)$', s)
    if not m:
        return s
    nums = "、".join(m.group(1).split())
    rest = m.group(3).strip()
    return f"{nums}，{rest}" if rest else nums


def _fix_inner_punct(s: str) -> str:
    """句内标点：列举用顿号，其余空格改逗号（已有标点处不动）"""
    s = _fix_enumerate(s)
    # 按已有分隔符切开，只在非分隔符片段里把空白换成逗号
    parts = re.split(r'([，。！？、；：,.!?;:])', s)
    out = []
    for i, p in enumerate(parts):
        if i % 2 == 0:
            p = re.sub(r'\s+', '，', p.strip())
        out.append(p)
    return "".join(out)


def _add_vocative_comma(s: str) -> str:
    """称呼语后补逗号：'妈妈 我中午就吃了半个馒头' → '妈妈，我中午就吃了半个馒头'。
    只取**最长**匹配的称呼（避免"妈妈"被拆成"妈"+"妈"重复加逗号）。"""
    # 取最长匹配的称呼；找到即定，不再回退到更短的词（否则"老板娘"会退化成"老板"）
    matched = None
    for v in sorted(_VOCATIVE, key=len, reverse=True):
        if s.startswith(v):
            matched = v
            break
    if not matched:
        return s
    # 剩余部分至少 2 字才视为"称呼+内容"，避免"老板娘在"被断成"老板，娘在"
    if len(s) < len(matched) + 2:
        return s
    nxt = s[len(matched)]
    if nxt in "，。！？、；：,.!?;:":
        return s                      # 已有标点，不重复添加
    return f"{matched}，{s[len(matched):].lstrip()}"


def _is_question(s: str) -> bool:
    """语境判断是否为疑问句"""
    core = s.rstrip("。！？，、,.!?").strip()
    if not core:
        return False
    # 1) 句首疑问副词："怎么跟我小时候这么像？"
    if core.startswith(_Q_START_WORDS):
        return True
    # 2) 结尾疑问字/词（排除"我不知道/告诉我…"这类陈述包裹）
    _wrap = ("知道", "告诉", "明白", "忘了", "记得", "猜")
    if core[-1] in _Q_END_CHARS and not any(w in core for w in _wrap):
        return True
    for w in _Q_END_WORDS:
        if core.endswith(w) and not any(w2 in core for w2 in _wrap):
            return True
    # 3) 反问标记
    if any(w in core for w in ("难道", "莫非", "岂能")):
        return True
    # 4) A不A 疑问结构："你会不会觉得我是骗子？"
    if re.search(r"(会不会|是不是|有没有|能不能|要不要|对不对|行不行|好不好|可不可以)", core):
        return True
    # 5) 疑问代词靠近句尾（末 5 字内），且不是"我不知道/告诉我"这类陈述包裹
    if any(w in core[-5:] for w in ("什么", "怎么", "为什么", "干吗", "干嘛", "多少", "哪里", "哪位", "是谁")):
        if not any(w in core for w in ("知道", "告诉", "明白", "忘了", "记得", "猜")):
            return True
    # 6) 短句"的"字结尾 + 含疑问代词："寻谁的？"
    if (core.endswith("的") and len(core) <= 8
            and any(w in core for w in ("谁", "什么", "哪儿", "哪里", "怎么", "多少"))):
        return True
    return False


def _is_exclam(s: str) -> bool:
    """语境判断是否为感叹/祈使句（含"知道了，快去"这类末分句祈使）"""
    core = s.rstrip("。！？，、,.!?").strip()
    if not core:
        return False

    def _hits_imp(seg: str) -> bool:
        return any(seg.startswith(w) for w in _IMPERATIVE)

    # 1) 整句句首祈使
    if _hits_imp(core):
        return True
    # 2) 末分句祈使："知道了，快去！"（只看最后一个分句，避免误判）
    tail_seg = core.split("，")[-1].strip()
    if tail_seg and tail_seg != core and _hits_imp(tail_seg):
        return True
    # 3) 结尾感叹词
    if core[-1] in _E_END_CHARS:
        return True
    # 4) 短句强情感：太…了 / 真是 / 多么 / 好不
    if len(core) <= 14 and re.search(r"太.+了|真是|好不|多么|可真是", core):
        return True
    return False


def _punctuate_sentence(s: str) -> str:
    """按语境补全标点：句内逗号/顿号 + 称呼逗号 + 句末。？！"""
    s = (s or "").strip()
    if not s:
        return ""
    if s[-1] in "。！？.!?":
        return _fix_inner_punct(s)
    s = _fix_inner_punct(s)
    s = _add_vocative_comma(s)
    if _is_question(s):
        return s + "？"
    if _is_exclam(s):
        return s + "！"
    return s + "。"


def _split_into_chapters(segments: List[tuple], gap_ms: int = 8000,
                         min_sent: int = 12, max_sent: int = 40) -> List[List[str]]:
    """按时间戳间隔把句子切成章节（对话停顿久 = 场景切换）。
    返回 [[句子, 句子...], [句子...], ...]"""
    if not segments:
        return []
    chapters, cur, last_end = [], [], None
    for start, end, text in segments:
        gap = (start - last_end) if last_end is not None else 0
        if gap > gap_ms and len(cur) >= min_sent:
            chapters.append(cur)
            cur = []
        elif len(cur) >= max_sent:
            chapters.append(cur)
            cur = []
        cur.append(text)
        last_end = end
    if cur:
        chapters.append(cur)
    # 过碎的章节合并到前一章（< min_sent 且不是唯一章节）
    merged = []
    for ch in chapters:
        if merged and len(ch) < min_sent:
            merged[-1].extend(ch)
        else:
            merged.append(ch)
    return merged


def _build_script_chapters(text: str, outdir: Path) -> List[Dict]:
    """构建"脚本化"章节结构：[{"title": 章节标题, "paragraphs": [段落...]}, ...]

    - 优先用 LLM 输出带章节小标题的 Markdown（解析成结构化数据）
    - 无 LLM 时用 SRT 时间间隔划分场景，标题为「场景 N」
    """
    # 1) LLM 优先：让模型直接给出带 ## 章节的完整脚本
    llm_md = llm_script(text)
    if llm_md:
        chapters, cur = [], None
        for line in llm_md.split("\n"):
            st = line.strip()
            if st.startswith("##"):
                if cur and cur["paragraphs"]:
                    chapters.append(cur)
                cur = {"title": st.lstrip("#").strip(), "paragraphs": []}
            elif st and cur is not None:
                cur["paragraphs"].append(st)
            elif st and cur is None:
                cur = {"title": "正文", "paragraphs": [st]}
        if cur and cur["paragraphs"]:
            chapters.append(cur)
        if chapters:
            return chapters

    # 2) 规则式：用 SRT 间隔划场景，逐句补标点
    segments = _load_srt_segments(outdir)
    groups = _split_into_chapters(segments)
    if not groups:
        # 无 SRT：整篇按句数切分
        sents = [s.strip() for s in re.split(r'[。！？\n]', text) if s.strip()]
        groups = [sents[i:i + 10] for i in range(0, len(sents), 10)] or []

    chapters = []
    for i, group in enumerate(groups, 1):
        paras, buf = [], []
        for s in group:
            buf.append(_punctuate_sentence(s))
            if len(buf) >= 4:          # 每 4 句一段
                paras.append("".join(buf))
                buf = []
        if buf:
            paras.append("".join(buf))
        if paras:
            kw = _chapter_keyword("".join(group))
            ttl = f"场景 {i}" + (f" · {kw}" if kw else "")
            chapters.append({"title": ttl, "paragraphs": paras})
    return chapters


def _chapter_keyword(text: str) -> str:
    """提取章节的实体关键词（人名/地名/机构/专名），用于生成章节小标题。"""
    try:
        import logging
        import jieba
        import jieba.posseg as pseg
        jieba.setLogLevel(logging.ERROR)

        freq: Dict[str, int] = {}
        for w, flag in pseg.cut(text):
            if len(w) >= 2 and flag in ("nr", "ns", "nt", "nz"):
                freq[w] = freq.get(w, 0) + 1
        if freq:
            return sorted(freq.items(), key=lambda kv: -kv[1])[0][0]
    except Exception:
        pass
    return ""


def generate_markdown(text: str, title: str, outdir: Path) -> Path:
    """将转写文本生成为 Markdown 文件（脚本化：分章节 + 段落）"""
    base = safe_slug(title) or "transcript"
    md_path = outdir / f"{base}.md"

    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> 来源：音视频转写")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    chapters = _build_script_chapters(text, outdir)
    if chapters:
        for ch in chapters:
            lines.append(f"## {ch['title']}")
            lines.append("")
            for para in ch["paragraphs"]:
                lines.append(para)
                lines.append("")
    else:
        # 兜底：用清洗后的段落文本
        for line in clean_transcript(text).split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
                lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    log(f"[Markdown] 已生成: {md_path}（{len(chapters)} 个章节）")
    return md_path


def _cjk_font_name() -> str:
    """按平台返回可用的中文字体名（避免日文字体渲染中文的问题）"""
    import platform as _pf
    sysname = _pf.system().lower()
    if sysname == "windows":
        return "微软雅黑"
    if sysname == "darwin":
        return "PingFang SC"
    return "WenQuanYi Micro Hei"


def _apply_cjk_font(doc, font_name: str) -> None:
    """修正 python-docx 默认模板的东亚字体绑定。

    根因：python-docx 自带 default.docx 的 theme 把 eastAsia 字体绑成日文字体
    （ＭＳ 明朝 / ＭＳ ゴシック），中文会以日文/旧字形渲染——看起来像繁体，
    但底层 Unicode 仍是简体（所以复制粘贴出来是简体）。
    这里把默认样式的 eastAsia 字体显式绑定为中文字体，并标记 zh-CN。
    """
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        style = doc.styles["Normal"]
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)

        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            rpr.append(lang)
        lang.set(qn("w:val"), "zh-CN")
        lang.set(qn("w:eastAsia"), "zh-CN")
    except Exception as e:
        log(f"[字体] 默认样式设置中文字体失败（不影响内容）: {e}")


def _set_run_font(run, font_name: str) -> None:
    """给单个 run 绑定中文字体（run 级设置优先级高于样式）"""
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        run.font.name = font_name
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)
    except Exception:
        pass


def generate_docx(text: str, title: str, outdir: Path) -> Path:
    """将转写文本生成为 DOCX 文件（自动清洗，完整段落）"""
    base = safe_slug(title) or "transcript"
    docx_path = outdir / f"{base}.docx"
    cleaned = clean_transcript(text)

    # 二次繁转简保障（三级兜底，确保 DOCX 绝不残留繁体）
    cleaned = _to_simplified(cleaned)

    # 尝试导入 python-docx，如果没有则安装
    try:
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        log("[依赖] 正在安装 python-docx...")
        _silent_run([PY, "-m", "pip", "install", "python-docx", "-q"])
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # 绑定中文字体（关键：默认模板绑的是日文字体，会让中文显示成繁体/日式字形）
    cjk = _cjk_font_name()
    _apply_cjk_font(doc, cjk)

    # 标题
    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for r in heading.runs:
        _set_run_font(r, cjk)

    # 元信息
    info_para = doc.add_paragraph()
    r1 = info_para.add_run("来源：音视频转写\n")
    r2 = info_para.add_run(f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}")
    for r in (r1, r2):
        _set_run_font(r, cjk)
        r.font.size = Pt(10)

    # 分隔线
    sep = doc.add_paragraph("_" * 50)
    for r in sep.runs:
        _set_run_font(r, cjk)

    # 正文：按章节 → 段落输出（章节用 Heading 2，正文首行缩进 2 字符 + 两端对齐）
    chapters = _build_script_chapters(text, outdir)

    if chapters:
        for ch in chapters:
            h = doc.add_heading(ch["title"], level=2)
            for r in h.runs:
                _set_run_font(r, cjk)
            for para_text in ch["paragraphs"]:
                p = doc.add_paragraph(para_text)
                _format_body_para(p, cjk)
    else:
        # 兜底：清洗后的段落
        for para_text in [x.strip() for x in cleaned.split("\n") if x.strip()]:
            p = doc.add_paragraph(para_text)
            _format_body_para(p, cjk)

    doc.save(str(docx_path))
    log(f"[DOCX] 已生成: {docx_path}（{cjk} · {len(chapters)} 章节）")
    return docx_path


def _format_body_para(p, cjk: str, size: float = 11) -> None:
    """正文段落排版：首行缩进 2 字符 + 1.5 倍行距 + 两端对齐（中文阅读习惯）"""
    try:
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        pf = p.paragraph_format
        pf.line_spacing = 1.5
        # 首行缩进 2 字符：字号 × 2（11pt × 2 = 22pt）
        pf.first_line_indent = Pt(size * 2)
        pf.space_after = Pt(6)          # 段间距，让章节更透气
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY   # 两端对齐
        for r in p.runs:
            _set_run_font(r, cjk)
            r.font.size = Pt(size)
    except Exception:
        pass


def _unify_output_names(outdir: Path, title: str, video_path: Optional[Path] = None) -> None:
    """把所有产物统一成主题名前缀，避免"视频用平台原始名、文档用主题名"的割裂。

    注意：必须在 generate_timestamped_txt 之后调用（该函数依赖固定名 subtitle.srt）。
    """
    base = safe_slug(title)
    if not base:
        return

    if video_path and Path(video_path).exists():
        vp = Path(video_path)
        new_vid = outdir / f"{base}{vp.suffix}"
        if os.path.abspath(new_vid) != os.path.abspath(vp):
            try:
                os.replace(str(vp), str(new_vid))
                log(f"[命名] 视频已统一命名: {new_vid.name}")
            except Exception as e:
                log(f"[命名] 视频重命名失败（不影响使用）: {e}")

    srt = outdir / "subtitle.srt"
    if srt.exists():
        try:
            os.replace(str(srt), str(outdir / f"{base}_subtitle.srt"))
        except Exception:
            pass


def generate_breakdown(text: str, title: str, outdir: Path) -> Path:
    """生成爆款文章拆解分析（结构化观点提炼）"""
    base = safe_slug(title) or "breakdown"
    breakdown_path = outdir / f"{base}_拆解.md"
    cleaned = clean_transcript(text)

    # 按句号/换行分割为句子
    sentences = re.split(r'[。！？\n]', cleaned)
    sentences = [s.strip() for s in sentences if s.strip()]

    lines = []
    lines.append(f"# {title} - 爆款拆解")
    lines.append("")
    lines.append(f"> 来源：音视频转写")
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. 核心观点提炼
    lines.append("## 一、核心观点")
    lines.append("")
    # 取前3个有实质内容的句子作为核心观点
    core_points = []
    for s in sentences:
        if len(s) >= 10 and s not in core_points:
            core_points.append(s)
        if len(core_points) >= 3:
            break
    for i, point in enumerate(core_points, 1):
        lines.append(f"{i}. {point}。")
    lines.append("")

    # 2. 金句摘录
    lines.append("## 二、金句摘录")
    lines.append("")
    golden_sentences = []
    for s in sentences:
        # 筛选有感染力或总结性的句子
        if any(kw in s for kw in ['所以', '其实', '关键', '真正', '因为', '但是', '然而']) and len(s) >= 15:
            golden_sentences.append(s)
        if len(golden_sentences) >= 5:
            break
    if not golden_sentences:
        # 兜底：取长度适中的句子
        golden_sentences = [s for s in sentences if 15 <= len(s) <= 50][:5]
    for i, sent in enumerate(golden_sentences, 1):
        lines.append(f"> {i}. {sent}")
    lines.append("")

    # 3. 结构分析
    lines.append("## 三、结构分析")
    lines.append("")
    # 按段落分组
    paragraphs = cleaned.split('\n')
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if len(paragraphs) <= 3:
        lines.append("- **开头**：引入话题，引发共鸣")
        lines.append("- **中间**：展开论述，层层递进")
        lines.append("- **结尾**：总结升华，留下余韵")
    else:
        # 简单分段：前1/4为开头，中间为展开，后1/4为结尾
        n = len(paragraphs)
        head_end = max(1, n // 4)
        tail_start = min(n - 1, 3 * n // 4)

        lines.append("### 开头部分（引发共鸣）")
        lines.append("")
        for p in paragraphs[:head_end]:
            lines.append(p)
            lines.append("")

        lines.append("### 主体部分（层层展开）")
        lines.append("")
        for p in paragraphs[head_end:tail_start]:
            lines.append(p)
            lines.append("")

        lines.append("### 结尾部分（总结升华）")
        lines.append("")
        for p in paragraphs[tail_start:]:
            lines.append(p)
            lines.append("")

    # 4. 关键词提取
    lines.append("## 四、关键词提炼")
    lines.append("")
    # 简单关键词提取：取出现频率较高的2-4字词
    word_freq = {}
    for s in sentences:
        # 提取2-4字词语（简单规则）
        words = re.findall(r'[\u4e00-\u9fa5]{2,4}', s)
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
    # 排序取前10个
    top_words = sorted(word_freq.items(), key=lambda x: -x[1])[:10]
    if top_words:
        words_str = "、".join([w for w, _ in top_words if len(w) >= 2])
        lines.append(f"**高频关键词**：{words_str}")
    else:
        lines.append("- 暂无高频词（文本较短或无明显关键词）")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*注：此分析为自动提炼，仅供参考。*")

    with open(breakdown_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"[拆解分析] 已生成: {breakdown_path}")
    return breakdown_path


def generate_timestamped_txt(outdir: Path, title: str = "transcript") -> Path:
    """生成带时间戳的 TXT 文件，格式：[HH:MM:SS.mmm --> HH:MM:SS.mmm] 原文"""
    base = safe_slug(title) or "transcript"
    txt_path = outdir / f"{base}_raw.txt"
    srt_path = outdir / "subtitle.srt"
    ts_txt_path = outdir / f"{base}_timestamped.txt"

    if not srt_path.exists():
        # 没有 SRT，直接复制原 TXT
        if txt_path.exists():
            shutil.copy2(txt_path, ts_txt_path)
        return ts_txt_path

    # 解析 SRT：序号行 / 时间戳行 / 文本行
    srt_content = srt_path.read_text(encoding="utf-8")
    # 时间戳正则：H:MM:SS,mmm --> H:MM:SS,mmm
    ts_pattern = re.compile(r'(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})')

    lines = srt_content.split("\n")
    segments = []
    i = 0
    while i < len(lines):
        m = ts_pattern.match(lines[i].strip())
        if m:
            sh, sm, ss, sms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            eh, em, es, ems = int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8))
            start_ms = sh * 3600000 + sm * 60000 + ss * 1000 + sms
            end_ms = eh * 3600000 + em * 60000 + es * 1000 + ems
            seg_text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != "":
                seg_text_lines.append(lines[i].strip())
                i += 1
            seg_text = " ".join(seg_text_lines)
            if seg_text:
                segments.append((start_ms, end_ms, seg_text))
        else:
            i += 1

    if not segments:
        if txt_path.exists():
            ts_txt_path.write_text(txt_path.read_text(encoding="utf-8"), encoding="utf-8")
        return ts_txt_path

    # 构建带时间戳的 TXT
    ts_start = lambda ms: f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d}.{ms%1000:03d}"
    ts_end = lambda ms: f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d}.{ms%1000:03d}"

    out_lines = []
    for start_ms, end_ms, seg_text in segments:
        out_lines.append(f"[{ts_start(start_ms)} --> {ts_end(end_ms)}] {seg_text}")

    ts_txt_path.write_text("\n".join(out_lines), encoding="utf-8")
    log(f"[时间戳TXT] 已生成: {ts_txt_path}")
    return ts_txt_path


# ==================== 入口 ====================
def main():
    ap = argparse.ArgumentParser(description="微信媒体 + 多平台视频/音乐下载")
    ap.add_argument("input", help="视频链接、公众号文章链接、视频文件或音频文件路径")
    ap.add_argument("--type", choices=["video", "music"], default=None,
                    help="指定类型：video=视频处理, music=音乐处理（默认自动检测）")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--no-whisper", action="store_true", help="跳过转写")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium"],
                    help="Whisper模型: tiny(~150MB), base(~1GB), small(~2GB,默认), medium(~5GB)")
    ap.add_argument("--lang", default="zh", help="语言: zh(中文), en(英文), auto(自动)")
    ap.add_argument("--denoise", action="store_true", help="启用降噪预处理（提升嘈杂环境识别率）")
    ap.add_argument("--format", choices=["txt", "md", "docx", "all"], default="all",
                    help="输出格式: txt=纯文本, md=Markdown, docx=Word文档, all=全部(默认)")
    ap.add_argument("--breakdown", action="store_true", help="生成爆款拆解分析（结构化观点提炼）")
    ap.add_argument("--llm", action="store_true",
                    help="启用 LLM 智能整理（默认关闭，全程走本地规则式处理）。"
                         "需先配置 LLM_API_KEY/LLM_BASE_URL，或写入 ~/.config/multi-media-processor/llm.json")
    args = ap.parse_args()

    # LLM 总开关：默认关闭，仅当用户显式加 --llm 时启用
    global LLM_ENABLED
    if args.llm:
        LLM_ENABLED = True
        if _load_llm_config():
            log("[LLM] 已按用户要求启用智能整理")
        else:
            log("[LLM] 已启用开关，但未检测到 API 配置（LLM_API_KEY 等），将回退本地规则式处理")
            LLM_ENABLED = False

    input_str = args.input.strip()

    # 检查输入是否为Cookie配置（双模式支持）
    if _handle_cookie_input(input_str):
        log("[Cookie] Cookie配置完成，下次使用时自动生效")
        sys.exit(0)

    # 判断输入类型：本地文件 vs URL
    is_local = is_local_file(input_str)
    is_url = not is_local and any(k in input_str.lower() for k in ["http://", "https://", "weixin.qq.com", "mp.weixin.qq.com"])

    # 确定输出目录
    if args.out:
        outdir = Path(args.out)
    elif is_sph(input_str):
        outdir = OUTPUT_ROOT / sph_id(input_str)
    elif is_mp(input_str):
        m = re.search(r"__biz=([^&]+)", input_str)
        name = m.group(1) if m else "mp_article"
        outdir = OUTPUT_ROOT / name
    elif is_local:
        # 本地文件：使用文件名作为目录名
        fname = Path(input_str).stem
        outdir = OUTPUT_ROOT / fname
    else:
        platform = detect_platform(input_str)
        outdir = OUTPUT_ROOT / platform

    os.makedirs(outdir, exist_ok=True)

    # ========== 本地文件处理 ==========
    if is_local:
        ext = Path(input_str).suffix.lower()
        log(f"[本地文件] {input_str}")
        log(f"[本地文件] 扩展名: {ext}")

        # 检查是否是音频或视频文件
        media_exts = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm',
                      '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a']
        if ext not in media_exts:
            log(f"[错误] 不支持的文件类型: {ext}")
            log(f"支持: {', '.join(media_exts)}")
            sys.exit(1)

        # 复制文件到输出目录
        src = Path(input_str).resolve()
        dest = outdir / src.name
        shutil.copy2(str(src), str(dest))
        log(f"[文件] 已复制到: {dest}")

        # 使用文件名作为初始标题（后续若无意义，转写后从内容重新提炼主题）
        init_title = safe_slug(src.stem) or "transcript"

        # Whisper 转写（用初始标题命名转写产物，避免与后续 {title}_raw.txt 重复）
        if not args.no_whisper:
            text = transcribe_whisper(dest, outdir, model=args.model, lang=args.lang,
                                      denoise=args.denoise, title=init_title)
            if text:
                # 确定最终标题：文件名有意义则用之，否则结合转写内容提炼主题
                title = init_title
                if not _is_meaningful_title(title):
                    title = _derive_title(text, fallback=init_title)
                    log(f"[命名] 文件名无明确主题，已按内容提炼标题: {title}")

                # 初始产物已由 transcribe_whisper 写出，这里按需重命名为最终主题名
                # （用 os.replace 而非 unlink——沙箱下删除会 SAFE_DELETE_FAIL_CLOSED，重命名则可用）
                transcript = outdir / f"{title}_raw.txt"
                _init_raw = outdir / f"{safe_slug(init_title) or 'transcript'}_raw.txt"
                if _init_raw.exists() and os.path.abspath(_init_raw) != os.path.abspath(transcript):
                    try:
                        os.replace(str(_init_raw), str(transcript))
                    except Exception:
                        transcript.write_text(text, encoding="utf-8")
                else:
                    transcript.write_text(text, encoding="utf-8")
                simp = _to_simplified(text)
                if simp != text:
                    (outdir / f"{title}_simplified.txt").write_text(simp, encoding="utf-8")
                log(f"[转写] 完成: {len(text)} 字")

                # 生成带时间戳的 TXT
                try:
                    generate_timestamped_txt(outdir, title)
                except Exception as e:
                    log(f"[时间戳TXT] 生成失败: {e}")

                # 生成 Markdown 和 DOCX
                if args.format in ("md", "all"):
                    generate_markdown(text, title, outdir)
                if args.format in ("docx", "all"):
                    generate_docx(text, title, outdir)
                if args.breakdown:
                    generate_breakdown(text, title, outdir)

                # 统一命名：视频/字幕也用主题名（须在 timestamped 生成之后）
                _unify_output_names(outdir, title, dest)

        log(f"{'='*50}")
        log(f"完成! 输出目录: {outdir}")
        log(f"{'='*50}")
        log("=== WX_MEDIA_DONE ===")
        return

    # ========== 音乐下载模式 ==========
    is_music = args.type == "music" or (args.type is None and not is_url)
    if is_music and not is_url:
        log("[音乐] 此功能已移除，请使用其他工具")
        sys.exit(1)

    # 微信视频号
    if is_sph(input_str):
        # 修复(2026-08-26): 原名单漏 whisper-cli 引擎且 "ffmpeg" 在 Windows 匹配不到 ffmpeg.exe，
        # 导致模型已下载但引擎缺失、转写 exit=1
        deps_to_check = _dep_names(
            "wx_video_download.exe",
            "ggml-small-q8_0.bin",
            "ffmpeg.exe",
            "whisper-cli.exe",
        )
        deps_to_check = [d for d in deps_to_check if d]
        ensure_runtime_deps(deps_to_check)
        # 修复(2026-08-26): 去掉 _IS_WINDOWS 门控——wx_channels_download 已全平台支持，
        # Cookie 配置检查（含 proxy 安全网）对 macOS/Linux 同样必要
        if not ensure_config():
            sys.exit("\n请先按上方提示配置 Cookie 后重试。")
        meta = process_sph(input_str, str(outdir))
        log(f"输出目录: {outdir}")
        log(f"类型: {meta['type']}")
        for k, v in meta.get("files", {}).items():
            log(f"  - {k}: {v}")
        log("=== WX_MEDIA_DONE ===")
        log("下一步：agent 读 transcript_raw.txt (+ metadata.json) 提炼『核心观点』写入 核心观点.md")
        return

    # 微信公众号
    if is_mp(input_str):
        meta = process_mp(input_str, str(outdir))
        log(f"输出目录: {outdir}")
        log(f"类型: {meta['type']}")
        for k, v in meta.get("files", {}).items():
            log(f"  - {k}: {v}")
        log("=== WX_MEDIA_DONE ===")
        log("下一步：agent 读 article.md (+ metadata.json) 提炼『核心观点』")
        return

    # 多平台视频
    platform = detect_platform(input_str)
    if platform == "unknown":
        log(f"[错误] 不支持的平台: {input_str}")
        log("支持: 微信视频号(sph) / 微信公众号(mp) / B站 / YouTube / 小红书 / TikTok / 微博 / Dailymotion / Vimeo")
        sys.exit(1)

    vid = extract_id(input_str, platform)
    log(f"{'='*50}")
    log(f"平台: {platform} | 视频ID: {vid}")
    log(f"{'='*50}")

    # 修复(2026-08-27): 原版所有同平台视频共用 OUTPUT_ROOT/platform 目录，
    # 多次运行产物互相覆盖（如 B 站两个视频的 transcript 冲掉彼此）。
    # 现改为 OUTPUT_ROOT/platform/vid 独立子目录，每个视频隔离。
    outdir = OUTPUT_ROOT / platform / vid
    os.makedirs(outdir, exist_ok=True)

    # 自动补全运行时依赖
    # 修复(2026-08-26): 补上 whisper-cli 引擎检查
    ensure_runtime_deps(_dep_names("ffmpeg.exe", "ggml-small-q8_0.bin", "whisper-cli.exe"))

    try:
        if platform == "xiaohongshu":
            vf = download_xiaohongshu(input_str, vid, outdir)
        elif platform == "douyin":
            vf = download_douyin(input_str, vid, outdir)
        else:
            vf = download_ytdlp(input_str, platform, vid, outdir)
    except Exception as e:
        print(f"[下载失败] {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    # Whisper 转写
    if not args.no_whisper and vf.exists():
        # 先取初始标题：文件名形如 bilibili_<内容标题>.mp4，去掉平台前缀更干净
        init_title = vf.stem
        _prefix = f"{platform}_"
        if init_title.startswith(_prefix):
            init_title = init_title[len(_prefix):]
        m = re.search(r"([^/\\]+?)(?:\.\w+)?$", str(vf))
        if m:
            _raw = m.group(1)
            if _raw.startswith(_prefix):
                _raw = _raw[len(_prefix):]
            if _raw:
                init_title = _raw

        # 清洗：去除 #话题标签 与 末尾视频 ID（抖音/部分平台标题常携带），避免文件名又长又脏
        _cleaned = _clean_video_title(init_title)
        if _cleaned:
            init_title = _cleaned

        # 用初始标题命名转写产物（避免与后续 {title}_raw.txt 重复）
        text = transcribe_whisper(vf, outdir, model=args.model, lang=args.lang,
                                  denoise=args.denoise, title=init_title)
        if text:
            title = init_title
            # 标题无明确语义时（如抖音无描述），结合转写内容提炼主题命名
            if not _is_meaningful_title(title):
                title = _derive_title(text, fallback=f"{platform}_{vid}")
                log(f"[命名] 视频无标题，已按内容提炼主题: {title}")

            # 生成 TXT：初始产物已由 transcribe_whisper 写出，这里按需重命名为最终主题名
            # （用 os.replace 而非 unlink——沙箱下删除会 SAFE_DELETE_FAIL_CLOSED，重命名则可用）
            transcript = outdir / f"{title}_raw.txt"
            _init_raw = outdir / f"{safe_slug(init_title) or 'transcript'}_raw.txt"
            if _init_raw.exists() and os.path.abspath(_init_raw) != os.path.abspath(transcript):
                try:
                    os.replace(str(_init_raw), str(transcript))
                except Exception:
                    transcript.write_text(text, encoding="utf-8")
            else:
                transcript.write_text(text, encoding="utf-8")
            simp = _to_simplified(text)
            if simp != text:
                (outdir / f"{title}_simplified.txt").write_text(simp, encoding="utf-8")
            log(f"[TXT] 已生成: {transcript}")

            # 生成带时间戳的 TXT
            try:
                generate_timestamped_txt(outdir, title)
            except Exception as e:
                log(f"[时间戳TXT] 生成失败: {e}")

            # 生成 Markdown
            if args.format in ("md", "all"):
                generate_markdown(text, title, outdir)

            # 生成 DOCX
            if args.format in ("docx", "all"):
                generate_docx(text, title, outdir)

            if args.breakdown:
                generate_breakdown(text, title, outdir)

            # 统一命名：视频/字幕也用主题名（须在 timestamped 生成之后）
            _unify_output_names(outdir, title, vf)

            log(f"转写已存: {transcript}")

    log(f"{'='*50}")
    log(f"完成! 输出目录: {outdir}")
    log(f"{'='*50}")
    log("=== WX_MEDIA_DONE ===")


def _dep_names(*names):
    """将依赖名列表按当前平台适配扩展名"""
    result = []
    for n in names:
        if n.endswith(".exe"):
            result.append(n if _IS_WINDOWS else n[:-4])
        else:
            result.append(n)
    return result


# 平台检测
_SYSTEM = __import__('platform').system().lower()
_IS_WINDOWS = _SYSTEM == "windows"
_IS_LINUX = _SYSTEM == "linux"
_IS_MACOS = _SYSTEM == "darwin"


if __name__ == "__main__":
    main()
