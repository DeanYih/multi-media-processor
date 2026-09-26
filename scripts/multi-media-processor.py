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


# ---------------- WorkBuddy 隔离 venv 自举 ----------------
def _wb_venv_python() -> Optional[str]:
    venv = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default"
    p = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(p) if p.exists() else None


def _wb_managed_python() -> Optional[str]:
    base = getattr(sys, "_base_executable", None)
    if base and "binaries/python/versions" in base:
        return base
    root = Path.home() / ".workbuddy" / "binaries" / "python" / "versions"
    if root.exists():
        vers = sorted(root.glob("*"), key=lambda p: p.name)
        for v in reversed(vers):
            cand = v / ("python.exe" if os.name == "nt" else "python")
            if cand.exists():
                return str(cand)
    return None


def _bootstrap_venv():
    venv_py = _wb_venv_python()
    cur = sys.executable.replace("\\", "/")
    if venv_py and cur == venv_py.replace("\\", "/"):
        return
    try:
        if not venv_py:
            managed = _wb_managed_python()
            if not managed:
                return
            venv = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default"
            subprocess.run([managed, "-m", "venv", str(venv)], check=True, timeout=300,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            venv_py = _wb_venv_python()
            if not venv_py:
                return
        if os.name == 'nt':
            sys.exit(subprocess.call([venv_py, *sys.argv]))
        else:
            os.execv(venv_py, [venv_py, *sys.argv])
    except Exception as e:
        print(f"[venv] 自动隔离失败，回退当前 Python: {e}", flush=True)


_bootstrap_venv()


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
import scene_audio  # 场景/音频识别增强（懒加载，缺失依赖时自动降级）

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
BILINGUAL_ENABLED = os.environ.get("BILINGUAL_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

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


# 元宝 Cookie 统一密钥名（走环境变量 / ~/.workbuddy/.secrets.env，不落盘进技能目录）
SPH_COOKIE_ENV = "WX_SPH_COOKIE"


def _load_secret_store():
    """懒加载统一密钥模块（不强制依赖，缺失时降级到直接读 .secrets.env）。"""
    lib = Path.home() / ".workbuddy" / "lib" / "secret_store.py"
    if not lib.exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("secret_store", str(lib))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _get_sph_cookie_secret() -> str:
    """统一密钥管理读取元宝 Cookie：环境变量 WX_SPH_COOKIE 优先，回退 ~/.workbuddy/.secrets.env。

    不在此函数里落盘——落盘由 _write_sph_cookie 注入到 exe 的运行时 config.yaml
    （exe 只认 config.yaml，不读环境变量）。"""
    # 1) 系统环境变量
    v = os.environ.get(SPH_COOKIE_ENV, "").strip()
    if v:
        return v
    # 2) 优先用统一密钥模块（与 secret_store 一致）
    ss = _load_secret_store()
    if ss is not None:
        try:
            v = (ss.get_secret(SPH_COOKIE_ENV) or "").strip()
            if v:
                return v
        except Exception:
            pass
    # 3) 直接解析回退文件兜底
    fb = Path.home() / ".workbuddy" / ".secrets.env"
    if fb.exists():
        try:
            for line in fb.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                if k.strip() == SPH_COOKIE_ENV:
                    return val.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _write_sph_cookie(cfg_path: Path, value: str) -> bool:
    """把元宝 Cookie 写入 exe 运行时 config.yaml（exe 只认 config.yaml，不读环境变量）。

    注意：sphCookie 是 cloudflare 块的子项（2 空格缩进），必须保留原行缩进，
    否则会被提到顶层，导致其后仍带缩进的 sphCredential 成为 YAML 孤儿，
    exe 启动报 'did not find expected key'。逐行处理以精确保留缩进。"""
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return False
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    lines = text.splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)sphCookie\s*:', line)
        if m:
            lines[i] = '%ssphCookie: "%s"\n' % (m.group(1), safe)
            found = True
            break
    if not found:
        lines.append('  sphCookie: "%s"\n' % safe)
    try:
        cfg_path.write_text(''.join(lines), encoding="utf-8")
        return True
    except Exception:
        return False


def _inject_sph_cookie_into_config() -> bool:
    """把 WX_SPH_COOKIE 注入到 exe 运行时 config.yaml；返回是否已就绪。"""
    cfg = TOOL_DIR / "config.yaml"
    if not cfg.exists():
        return False
    env_cookie = _get_sph_cookie_secret()
    if not env_cookie:
        return False
    if env_cookie == _read_sph_cookie(cfg):
        return True
    return _write_sph_cookie(cfg, env_cookie)


def _clear_sph_cookie_in_config() -> bool:
    """下载完成后清空运行时 config.yaml 的明文 sphCookie，避免凭证长期落盘在技能目录。

    安全依据：exe 启动即把 cookie 载入内存，清空文件不影响本次及后续同会话下载；
    新会话由 _inject_sph_cookie_into_config 从 WX_SPH_COOKIE 重新注入。
    注意：保留原行缩进（sphCookie 是 cloudflare 子项），逐行处理避免破坏 YAML 结构。"""
    cfg = TOOL_DIR / "config.yaml"
    if not cfg.exists():
        return False
    try:
        text = cfg.read_text(encoding="utf-8")
    except Exception:
        return False
    if re.search(r'^\s*sphCookie\s*:\s*""\s*$', text, re.MULTILINE):
        return True  # 已是空值
    lines = text.splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)sphCookie\s*:', line)
        if m:
            lines[i] = '%ssphCookie: ""\n' % m.group(1)
            found = True
            break
    if not found:
        lines.append('  sphCookie: ""\n')
    try:
        cfg.write_text(''.join(lines), encoding="utf-8")
        return True
    except Exception:
        return False


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
    # 元宝 Cookie 走统一密钥管理：环境变量 WX_SPH_COOKIE -> ~/.workbuddy/.secrets.env
    # 不落盘进技能目录 config.yaml。exe 只认 config.yaml，故运行时注入。
    env_cookie = _get_sph_cookie_secret()
    cfg_cookie = _read_sph_cookie(tool_cfg)
    if env_cookie:
        if _write_sph_cookie(tool_cfg, env_cookie):
            log("[配置] 已从 WX_SPH_COOKIE 注入 sphCookie 到运行时配置（exe 读取用）")
        return True
    if cfg_cookie:
        # 兼容旧明文配置（config.yaml 仍残留明文）——给出迁移提醒
        log("[配置] 警告：当前使用 config.yaml 内的明文 sphCookie，建议改用环境变量 "
            "WX_SPH_COOKIE（密钥不落盘）。")
        return True
    log("[配置] 缺少元宝 Cookie。请写入 ~/.workbuddy/.secrets.env：")
    log("  追加一行  WX_SPH_COOKIE=<k=v;k=v 字符串>")
    log("  （不要 setx：该 Cookie 超长会触发 Windows 1024 字符上限被截断；.secrets.env 无此限制）")
    log("  获取方式：浏览器登录 yuanbao.tencent.com → F12 → Application → Cookies → "
        "复制 .tencent.com 域下全部值拼成 k=v;k=v")
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
    # 注入元宝 Cookie 到运行时 config.yaml（exe 只认 config.yaml，不读环境变量）
    if not _inject_sph_cookie_into_config():
        log("[tool] 提示：未找到 WX_SPH_COOKIE，视频号解析可能在 parse_sph 阶段报 Cookie 缺失")
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
def process_sph(url, outdir, lang="zh", model="small", enhance=False):
    """视频号解析：本地 wx_video_download.exe + 本地元宝 Cookie（WX_SPH_COOKIE）。

    单一通道（已实测可下载真实视频）：启动本地 exe（监听 127.0.0.1:2022）→
    调用 /api/channels/parse_sph，用本地元宝 Cookie 换出视频直链 → 下载 →
    本地 Whisper 转写 → 产物落盘。"""
    ensure_tool()
    log("\n=== 1. parse_sph 解析原地址（本地 exe + 本地元宝 Cookie） ===")
    r = httpx.get(PARSE_SPH, params={"url": url}, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        # 修复(2026-08-26): 原 assert 直接抛原始 JSON；Cookie 类错误给出明确指引
        msg = str(data)
        if "cookie" in msg.lower():
            log(f"[解析失败] {msg[:300]}")
            sys.exit("\nCookie 缺失或已失效：请写入 ~/.workbuddy/.secrets.env 的 "
                     "WX_SPH_COOKIE（元宝 Cookie 会定期过期，重新按 F12 抓取后覆盖该行即可；"
                     "注意不要用 setx，超长会被 1024 上限截断）。")
        raise RuntimeError(f"解析失败: {data}")
    feed = data["data"]["data"]["feedInfo"]
    author = data["data"]["data"]["authorInfo"]["nickname"]
    desc = feed.get("description", "")
    video_url = feed["h264VideoInfo"]["videoUrl"]
    log(f"作者: {author}")
    log(f"描述: {desc}")
    log(f"videoUrl: {video_url[:90]}...")

    return _sph_download_transcribe(outdir, lang, model, enhance, author, desc, video_url, url)




def _sph_download_transcribe(outdir, lang, model, enhance, author, desc, video_url, url):
    """视频号下载 + 本地 Whisper 转写 + 产物落盘（本地 exe 模式使用）。"""
    mp4 = os.path.join(outdir, "video.mp4")
    # 下载视频直链（video_url 来自 parse_sph，腾讯 CDN 直链；带 UA 防 403）
    if not os.path.exists(mp4) or os.path.getsize(mp4) == 0:
        log(f"\n=== 2. 下载视频 ===")
        try:
            with httpx.stream("GET", video_url, headers={"User-Agent": UA},
                              follow_redirects=True, timeout=300) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                log(f"[下载] 目标: {mp4} ({total/1024/1024:.1f} MB)")
                with open(mp4, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            if os.path.getsize(mp4) == 0:
                raise RuntimeError("下载文件大小为 0")
            log(f"[下载] 完成 ({os.path.getsize(mp4)/1024/1024:.1f} MB)")
        except Exception as e:
            log(f"[下载] 失败: {e}")
            raise
    else:
        log(f"[下载] 已存在，跳过")
    log(f"\n=== 3. Whisper 本地转写 ({model}, {lang}) ===")
    transcript = os.path.join(outdir, "transcript_raw.txt")
    work = os.path.join(str(WHISPER_WORK), sph_id(url))

    # 增强：唱歌/带BGM 视频先做人声分离，再送 Whisper
    audio_src = mp4
    if enhance:
        try:
            if scene_audio.audio_has_music_or_singing(mp4):
                log("[增强] 检测到音乐/唱歌，尝试人声分离（demucs）...")
                v = scene_audio.separate_vocals(mp4, outdir)
                if v:
                    audio_src = v
                    log(f"[增强] 已切换到人声轨: {os.path.basename(v)}")
                else:
                    log("[增强] 人声分离不可用/失败，回退原音轨")
            else:
                log("[增强] 未检测到音乐/唱歌，跳过人声分离")
        except Exception as e:
            log(f"[增强] 人声分离跳过: {e}")

    try:
        _silent_check_call([
            PY, str(WHISPER_DIR / "scripts" / "transcribe.py"), audio_src,
            "--model", model, "--lang", lang,
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
        simp = _to_simplified(raw)
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

    # 安全收尾：清空运行时 config.yaml 明文 sphCookie（已注入 exe 内存，不影响后续）
    try:
        _clear_sph_cookie_in_config()
    except Exception:
        pass

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


def transcribe_whisper(video_path: Path, outdir: Path, model: str = "small", lang: str = "zh", denoise: bool = False, title: str = "transcript", vocal_sep: bool = False, threads: Optional[int] = None, prompt: Optional[str] = None) -> str:
    log(f"[Whisper] 转写中 ({model}, {lang})...")
    # 人声分离前处理（唱歌/带BGM 场景）：先分离人声再转写，显著降低乱码
    src_for_transcribe = video_path
    if vocal_sep and scene_audio.vocal_sep_available():
        try:
            if scene_audio.audio_has_music_or_singing(str(video_path)):
                log("[增强] 检测到音乐/唱歌，执行人声分离前处理...")
                vocals = scene_audio.separate_vocals(str(video_path), outdir)
                if vocals:
                    src_for_transcribe = Path(vocals)
                    log(f"[增强] 改用分离人声轨转写: {vocals}")
        except Exception as e:
            log(f"[增强] 人声分离跳过（回退原轨）: {e}")
    work = WHISPER_WORK / video_path.stem
    # raw.txt 按主题命名（避免与 {title}_raw.txt 重复）；
    # subtitle.srt 保持固定名，generate_timestamped_txt 依赖它
    transcript = outdir / f"{safe_slug(title) or 'transcript'}_raw.txt"
    srt_path = outdir / "subtitle.srt"
    try:
        cmd = [
            PY, str(WHISPER_DIR / "scripts" / "transcribe.py"),
            str(src_for_transcribe),
            "--model", model, "--lang", lang,
            "--out", str(transcript),
            "--out-srt", str(srt_path),
            "--exe", str(WHISPER_CLI),
            "--work", str(work),
        ]
        if denoise:
            cmd.append("--denoise")
        if threads:
            cmd += ["--threads", str(threads)]
        if prompt:
            cmd += ["--prompt", prompt]
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


# ============================================================
# ============================================================
# 术语替换（ASR 同音/近音错误修正，支持多领域自动检测）
# ============================================================
_SCRIPT_DIR = Path(__file__).parent
_TERM_BUILTIN_DIR = _SCRIPT_DIR / "terminology"
_TERM_CACHE: Dict[str, tuple] = {}  # {file_path: (mtime, {wrong: right, ...})}

# 领域关键词表（弱信号，用于 fallback 检测）
_DOMAIN_KEYWORDS: Dict[str, list] = {}
# 领域 wrong-form 索引（强信号，直接匹配错误词）
_DOMAIN_WRONG_FORMS: Dict[str, set] = {}
# 领域检测阈值
_DOMAIN_THRESHOLD = 2            # 长文本 (≥200字)
_DOMAIN_THRESHOLD_SHORT = 1      # 短文本 (<200字)
_SHORT_TEXT_LEN = 200


def _init_domain_dicts():
    """预加载所有领域词典的关键词和 wrong-form 索引。
    关键词去重并过滤至 2+ 字；wrong-form 也过滤至 2+ 字避免单字误伤。"""
    if _DOMAIN_KEYWORDS or _DOMAIN_WRONG_FORMS:
        return
    for fname in _TERM_BUILTIN_DIR.glob("*.json"):
        stem = fname.stem
        if stem == "common":
            continue
        try:
            with open(fname, encoding="utf-8") as f:
                data = json.load(f)
            # 关键词：去重 + 2+ 字过滤
            kws = data.get("keywords", [])
            if kws:
                _DOMAIN_KEYWORDS[stem] = list(dict.fromkeys(kw for kw in kws if len(kw) >= 2))
            # wrong-form：从 replacements 的 key 提取，过滤 2+ 字
            raw_repl = data.get("replacements", {})
            wrongs = {k for k, v in raw_repl.items() if k != v and len(k) >= 2}
            if wrongs:
                _DOMAIN_WRONG_FORMS[stem] = wrongs
        except Exception:
            pass


def _detect_domain(text: str) -> list:
    """智能检测领域，两阶段策略：
    1. 强信号：文本中出现任何领域的 wrong form → 该领域命中（最高置信度）
    2. 弱信号：关键词匹配（短文本阈值1，长文本阈值2）
    返回匹配的领域名列表（不含 common），支持多领域叠加。"""
    _init_domain_dicts()
    matched = set()
    # Pass 1: wrong-form scan（最强信号 — wrong form 只可能来自该领域）
    for domain, wrongs in _DOMAIN_WRONG_FORMS.items():
        if any(wrong in text for wrong in wrongs):
            matched.add(domain)
    # Pass 2: keyword matching（回退策略）
    threshold = _DOMAIN_THRESHOLD_SHORT if len(text) < _SHORT_TEXT_LEN else _DOMAIN_THRESHOLD
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if domain in matched:
            continue
        hits = sum(1 for kw in keywords if kw in text)
        if hits >= threshold:
            matched.add(domain)
    return sorted(matched)


def _load_dict(path: Path) -> Dict[str, str]:
    """加载单个词典 JSON，返回 {错误词: 正确词}，按词长降序排列。
    过滤自指条目和单字条目（单字替换极易误伤）。"""
    if not path.exists():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _TERM_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("replacements", {})
        # 过滤自指 + 单字 + 按词长降序
        replacements = {k: v for k, v in raw.items() if k != v and len(k) >= 2}
        replacements = dict(sorted(replacements.items(), key=lambda x: -len(x[0])))
        _TERM_CACHE[str(path)] = (mtime, replacements)
        return replacements
    except Exception as e:
        log(f"[术语] 加载失败 {path.name}: {e}")
        return {}


def _load_terminology(text: str, term_path: Optional[str] = None) -> Dict[str, str]:
    """加载术语词典，支持自动领域检测 + 手动指定。

    加载策略：
    1. common.json（always_load，始终加载）
    2. 自动检测：根据文本关键词加载匹配领域的词典
    3. --terms 手动指定：加载指定路径（覆盖自动检测）

    多词典结果合并，冲突时后加载的覆盖前面的。
    """
    if term_path:
        # 手动指定路径
        p = Path(term_path)
        if p.exists() and p.suffix == ".json":
            return _load_dict(p)
        log(f"[术语] 指定路径不存在或不是 JSON: {term_path}")
        return {}

    merged: Dict[str, str] = {}

    # 1. common.json（始终加载）
    common_path = _TERM_BUILTIN_DIR / "common.json"
    common_repl = _load_dict(common_path)
    if common_repl:
        merged.update(common_repl)

    # 2. 自动检测领域
    domains = _detect_domain(text)
    for domain in domains:
        dp = _TERM_BUILTIN_DIR / f"{domain}.json"
        d_repl = _load_dict(dp)
        if d_repl:
            merged.update(d_repl)

    return merged


# ---- 反向验证：替换后异常模式扫描（只告警不修改）----
# 中文中不应连续出现的字（连续出现几乎必为 ASR 错误或替换副作用）
_SANE_REPEAT_CHARS = "的是了在都有也都不"
# 断裂词模式（仅含非重复字符组合，重复字符由上面的 repeat 检查覆盖）
_SANE_BROKEN_WORDS = ["不的"]


def _post_correction_sanity_check(text: str, count: int) -> list:
    """替换后异常模式扫描（只告警不修改文本）。

    检测两类异常：
    1. 重复字符：中文中不应连续出现的字（如"的的""是是"）
    2. 断裂词：标准中文中不应出现的组合（如"不的"应为"不是"或"不能"）

    返回告警列表，为空表示无异常。不修改原文，仅作为安全网记录。
    """
    warnings = []
    # 1. 异常重复字符
    for ch in _SANE_REPEAT_CHARS:
        if ch + ch in text:
            warnings.append(f"重复字符「{ch}{ch}」")
    # 2. 断裂词模式
    for broken in _SANE_BROKEN_WORDS:
        if broken in text:
            warnings.append(f"断裂词「{broken}」")
    return warnings


def _apply_terminology(text: str, term_path: Optional[str] = None) -> tuple:
    """应用术语替换修正 ASR 转写错误。
    返回 (修正后文本, 替换次数, 检测到的领域列表)。
    替换完成后执行反向验证扫描，异常模式记录到日志（不修改文本）。"""
    if term_path:
        # 手动指定路径模式
        replacements = _load_terminology(text, term_path)
        if not replacements:
            return text, 0, []
        count = 0
        for wrong, right in replacements.items():
            n = text.count(wrong)
            if n > 0:
                text = text.replace(wrong, right)
                count += n
        # 反向验证
        warnings = _post_correction_sanity_check(text, count)
        if warnings:
            log(f"[术语] 反向验证告警: {'; '.join(warnings)}")
        return text, count, []

    # 自动检测模式：先检测领域，再加载并应用
    domains = _detect_domain(text)
    replacements = _load_terminology(text, term_path)
    if not replacements:
        return text, 0, domains
    count = 0
    for wrong, right in replacements.items():
        n = text.count(wrong)
        if n > 0:
            text = text.replace(wrong, right)
            count += n
    # 反向验证
    warnings = _post_correction_sanity_check(text, count)
    if warnings:
        log(f"[术语] 反向验证告警: {'; '.join(warnings)}")
    return text, count, domains


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
    if cfg and USE_LLM:
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

    # 优先 LLM 智能整理（断句 + 标点 + 分段），仅当 --llm 显式开启时
    if USE_LLM and len(text) >= 30:
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
    """解析 SRT 字幕，返回 [(start_ms, end_ms, text), ...]
    优先 subtitle.srt；不存在时回退 *_raw.srt（sph 视频号流程的产物名）。"""
    srt_path = outdir / "subtitle.srt"
    if not srt_path.exists():
        # 回退：sph 等流程生成 transcript_raw.srt / *_raw.srt
        alt = sorted(outdir.glob("*_raw.srt")) or sorted(outdir.glob("*.srt"))
        if alt:
            srt_path = alt[0]
    if not srt_path.exists():
        return []
    try:
        content = srt_path.read_text(encoding="utf-8")
    except Exception:
        return []
    # 先繁转简（Whisper 输出繁体时处理）
    content = _to_simplified(content)
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


# Global flags
BILINGUAL_ENABLED = os.environ.get("BILINGUAL_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
USE_LLM = False  # Set by main() when --llm flag is passed
_TRANSLATE_CACHE = {}


def _translate_text(text: str, source_lang: str = "auto", target_lang: str = "zh-CN") -> str:
    """Translate text using available service. Returns translated text or original if translation fails.
    
    Priority:
    1. LLM API if configured and BILINGUAL_ENABLED
    2. MyMemory Translate (free, no key needed) with proxy bypass
    3. deep_translator with proxy bypass (proxies={})
    4. Return original text if all fail
    """
    if not text or not text.strip():
        return text
    key = f"{source_lang}|{target_lang}|{text}"
    if key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[key]
    result = _translate_text_uncached(text, source_lang, target_lang)
    _TRANSLATE_CACHE[key] = result
    return result


_translate_progress = {"count": 0}


def _translate_text_uncached(text: str, source_lang: str = "auto", target_lang: str = "zh-CN") -> str:
    if not text or not text.strip():
        return text
    
    # Try LLM first if available (only when --llm explicitly enabled)
    if USE_LLM and BILINGUAL_ENABLED:
        cfg = _load_llm_config()
        if cfg and text:
            try:
                import httpx
                base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
                url = base + "/chat/completions"
                headers = {
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": cfg.get("model", "gpt-4o-mini"),
                    "messages": [
                        {"role": "system", "content": "You are a professional translator. Translate the following text to Chinese (Simplified). Output ONLY the translation, nothing else."},
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.3,
                }
                import json as _json
                resp = httpx.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code == 200:
                    result = resp.json()["choices"][0]["message"]["content"].strip()
                    if result and len(result) > 1:
                        return result
            except Exception as e:
                log(f"[翻译] LLM失败: {e}")
    
    # Try MyMemory Translate (free, no key, supports Chinese)
    try:
        import requests
        url = "https://api.mymemory.translated.net/get"
        src = source_lang if source_lang != "auto" else "en"
        tgt = target_lang.replace("zh-CN", "zh").replace("en-US", "en")
        langpair = f"{src}|{tgt}"
        params = {"q": text, "langpair": langpair}
        resp = requests.get(url, params=params, timeout=5, headers={"User-Agent": "Mozilla/5.0"}, proxies={"http": None, "https": None})
        if resp.status_code == 200:
            result = resp.json().get("responseData", {}).get("translatedText", "")
            if result and len(result) > 1 and "INVALID" not in result.upper() and "ERROR" not in result.upper():
                return result
    except Exception as e:
        log(f"[翻译] MyMemory失败: {e}")
    
    return text


def _is_english_text(s: str) -> bool:
    """Check if text is primarily English (contains significant Latin characters)."""
    if not s:
        return False
    # Count Latin characters vs CJK
    latin_count = sum(1 for c in s if c.isascii() and c.isalpha())
    cjk_count = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    total_alpha = latin_count + cjk_count
    if total_alpha == 0:
        return False
    return latin_count / total_alpha > 0.5


def _split_mixed_lines(text: str) -> List[tuple]:
    """Split text into (original, translated) pairs.
    Returns list of tuples: (original_text, chinese_translation_or_empty)
    """
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            lines.append((line, ''))
            continue
        
        # Check if line is primarily English
        if _is_english_text(line):
            translated = _translate_text(line)
            lines.append((line, translated))
        else:
            # Pure Chinese or mixed: keep original
            lines.append((line, ''))
    
    return lines


def _is_cjk_text(s: str) -> bool:
    """是否含中文字符（决定是否走中文标点规则）。
    英文等拉丁文本应保留原标点，不能被中文标点规则改写。"""
    return any('\u4e00' <= c <= '\u9fff' for c in (s or ""))


def _punctuate_sentence(s: str) -> str:
    """按语境补全标点：句内逗号/顿号 + 称呼逗号 + 句末。？！
    仅对含中文的文本生效；纯英文/拉丁文本原样返回（保留英文标点与空格）。"""
    s = (s or "").strip()
    if not s:
        return ""
    # 非中文文本：不做中文标点化，避免空格被误替换为中文逗号
    if not _is_cjk_text(s):
        return s
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


# ============================================================
# 语义章节划分（v1.3.5+）：TextTiling 词汇相似度 + 时间停顿 → 两级层次
# ============================================================

# 语义切分停用词（虚词/代词/副词——不携带话题信息）
_SEM_STOPWORDS: frozenset = frozenset({
    '我们', '你们', '他们', '咱们', '这个', '那个', '这些', '那些', '这样', '那样',
    '一个', '两个', '几个', '什么', '怎么', '为什么', '哪里', '哪个', '哪些',
    '的话', '是吧', '对吧', '好吧', '然后', '但是', '所以', '因为', '如果',
    '还有', '或者', '而且', '不过', '其实', '反正', '就是',
    '非常', '特别', '特别大', '有点', '一点', '一些', '一下', '一直', '已经',
    '现在', '当时', '一会', '一会儿', '等会', '马上', '直接', '肯定',
    '可能', '应该', '必须', '需要', '进行', '开始', '情况', '样子', '模样',
    '地方', '时候', '玩意', '知道', '觉得', '感觉', '认为', '发现', '看到',
    '东西', '问题', '一模一样', '百分之百', '不容易', '搞的', '弄的',
})

# 有效 POS：名词类 + 动词类 + 英文（携带话题信息）
_SEM_POS = ('n', 'nr', 'ns', 'nt', 'nz', 'vn', 'v', 'eng')

# 报错代码 → 归一化实体
_ERROR_CODES = frozenset({'C0', 'C1', 'A0', 'A1', 'B0', 'B1', 'C0C1', 'C1C0'})

# 动词 → 名词化动作映射（用于概括标题）
_VERB_NOMINAL = {
    '测': '测试', '跑': '测试', '试': '测试', '测试': '测试', '检测': '测试',
    '换': '更换', '更换': '更换', '替换': '更换',
    '修': '维修', '维修': '维修', '焊': '焊接', '焊接': '焊接', '吹': '焊接',
    '看': '检查', '检查': '检查', '查': '检查', '观察': '检查',
    '拆': '拆解', '抠': '拆解', '拆机': '拆解',
    '装': '组装', '组装': '组装', '装机': '组装',
    '分析': '分析', '判断': '判断', '排查': '排查', '确认': '确认',
    '翻新': '翻新', '清理': '清理',
}

# 结果标记词 → 标题后缀
_RESULT_MARKERS = [
    (('白瞎', '失败', '一模一样', '白费', '判死刑'), '失败'),
    (('搞定', '完美', '上岸', '不容易', '应该是搞定了'), '搞定'),
]

# 故障现象词（单实体标题时补充"故障"单元）
_FAULT_WORDS = ('挂了', '坏了', '不亮', '死机', '花屏', '黑屏', '报错', '故障')

# 标题弱词黑名单（跨领域词典混入的泛化词，不进标题）
_TITLE_WEAK_WORDS = frozenset({
    '代表', '象征', '标志', '方面', '角度', '内容', '形式', '形态',
    '单位', '部门', '领域', '范围', '程度', '过程', '阶段', '环境',
})

# 领域实体集缓存（从术语词典 keywords 动态合并，跨领域通用）
_DOMAIN_ENTITY_CACHE: Optional[frozenset] = None


def _domain_entity_set() -> frozenset:
    """合并所有领域词典的 keywords 作为章节标题的领域实体加权表。"""
    global _DOMAIN_ENTITY_CACHE
    if _DOMAIN_ENTITY_CACHE is None:
        ents = set()
        try:
            _init_domain_dicts()
            for kws in _DOMAIN_KEYWORDS.values():
                ents.update(kws)
        except Exception:
            pass
        _DOMAIN_ENTITY_CACHE = frozenset(ents)
    return _DOMAIN_ENTITY_CACHE


def _sem_seg_words(sent: str) -> list:
    """句子分词，保留携带话题信息的词（用于相似度计算）"""
    try:
        import jieba.posseg as pseg
        return [w for w, flag in pseg.cut(sent)
                if len(w) >= 2 and flag in _SEM_POS and w not in _SEM_STOPWORDS]
    except Exception:
        return [w for w in re.split(r'\s+', sent) if len(w) >= 2]


def _detect_boundaries(segments: List[tuple], win: int = 3) -> list:
    """相邻窗口词汇相似度（Jaccard）+ 时间停顿加成 → 每个句间位置的边界强度 [0,1]"""
    n = len(segments)
    words = [_sem_seg_words(t) for _, _, t in segments]
    scores = []
    for i in range(1, n):
        left = [w for ws in words[max(0, i - win):i] for w in ws]
        right = [w for ws in words[i:i + win] for w in ws]
        if not left or not right:
            sim = 0.0
        else:
            ls, rs = set(left), set(right)
            sim = len(ls & rs) / len(ls | rs)
        time_gap = segments[i][0] - segments[i - 1][1]
        time_boost = min(1.0, time_gap / 6000) * 0.4 if time_gap > 3000 else 0.0
        scores.append(1.0 - sim + time_boost)
    if not scores:
        return []
    mx = max(scores) or 1.0
    return [s / mx for s in scores]


def _semantic_chapters(segments: List[tuple], min_sents: int = 20,
                       max_sents: int = 60, target_chapters=None) -> List[Dict]:
    """自适应语义切分（核心章节版）：按边界分数选 top-N 强边界（间距≥min_sents）→
    少量一级核心章节；仅超长章节（>45 句）才细分二级小节。
    返回 [{"level", "range", "start_ms", "end_ms"}, ...] 顺序混排。"""
    n = len(segments)
    if n == 0:
        return []
    bscores = _detect_boundaries(segments)

    # 自适应强边界数量：约每 40 句一章（核心章节，方便快速查看重点）
    if target_chapters is None:
        target_chapters = max(3, round(n / 40))
    cands = sorted(range(1, n), key=lambda i: -bscores[i - 1])
    picked = []
    for i in cands:
        if len(picked) >= target_chapters:
            break
        if all(abs(i - p) >= min_sents for p in picked):
            picked.append(i)
    picked.sort()
    bounds = [0] + picked + [n]
    # 尾章过短则并入前章
    if len(bounds) > 2 and n - bounds[-2] < min_sents // 2:
        bounds.pop(-2)

    result = []
    for ci in range(len(bounds) - 1):
        s, e = bounds[ci], bounds[ci + 1]
        # 二级小节：仅超长章节（>45 句）细分，最多 3 个小节
        if e - s > 45:
            sub_cands = sorted((i for i in range(s + 1, e)), key=lambda i: -bscores[i - 1])
            sub_picked = []
            want = min(3, max(2, (e - s) // 22))  # 每小节约22句，至多3个
            for i in sub_cands:
                if len(sub_picked) >= want - 1:
                    break
                if all(abs(i - p) >= 10 for p in sub_picked) and i - s >= 8 and e - i >= 8:
                    sub_picked.append(i)
            sub_picked.sort()
            sub_bounds = [s] + sub_picked + [e]
            for si in range(len(sub_bounds) - 1):
                ss, se = sub_bounds[si], sub_bounds[si + 1]
                result.append({"level": 1 if si == 0 else 2, "range": (ss, se),
                               "start_ms": segments[ss][0], "end_ms": segments[se - 1][1]})
        else:
            result.append({"level": 1, "range": (s, e),
                           "start_ms": segments[s][0], "end_ms": segments[e - 1][1]})

    # 超长兜底拆分
    final = []
    for ch in result:
        s, e = ch["range"]
        if e - s > max_sents:
            for k in range(s, e, max_sents):
                ke = min(k + max_sents, e)
                final.append({**ch, "range": (k, ke),
                              "start_ms": segments[k][0], "end_ms": segments[ke - 1][1],
                              "level": 1 if k == s else ch["level"]})
        else:
            final.append(ch)
    # 尾段碎片（<5句）并回前一段
    if len(final) >= 2:
        s, e = final[-1]["range"]
        if e - s < 5:
            prev = final[-2]
            ps, _ = prev["range"]
            final[-2] = {**prev, "range": (ps, e), "end_ms": final[-1]["end_ms"]}
            final.pop()
    return final


def _chapter_summary_title(segments: List[tuple], s: int, e: int,
                           domain_ents: frozenset) -> str:
    """动作+实体组合概括标题（规则式）：如「显存更换与报错测试·失败」+ 时间戳前缀"""
    from collections import Counter
    text = ' '.join(t for _, _, t in segments[s:e])

    # 结果后缀检测
    result_suffix = ''
    for marker_words, suffix in _RESULT_MARKERS:
        if any(mw in text for mw in marker_words):
            result_suffix = suffix
            break

    # (动作, 实体) 共现统计：句内×3，跨句借用×1
    pairs: Counter = Counter()
    ent_freq: Counter = Counter()
    act_freq: Counter = Counter()
    last_ents: list = []
    try:
        import jieba.posseg as pseg
        for sent in text.split(' '):
            cur_acts, cur_ents = [], []
            for w, flag in pseg.cut(sent):
                if w in _SEM_STOPWORDS:
                    continue
                if w in _ERROR_CODES:
                    w = '报错'
                if flag in ('v', 'vn'):
                    base = w.rstrip('过了着')
                    act = _VERB_NOMINAL.get(w) or _VERB_NOMINAL.get(base)
                    if act:
                        cur_acts.append(act)
                    continue
                if (flag in ('n', 'nr', 'ns', 'nt', 'nz', 'eng')
                        and w in domain_ents and len(w) >= 2
                        and w not in _TITLE_WEAK_WORDS):
                    cur_ents.append(w)
                    ent_freq[w] += 3
            pair_ents = cur_ents or last_ents
            borrow_wgt = 3 if cur_ents else 1
            for a in set(cur_acts):
                act_freq[a] += 1
                for ent in pair_ents:
                    pairs[(a, ent)] += borrow_wgt
            if cur_ents:
                last_ents = cur_ents
    except Exception:
        pass

    # 标题拼装：动作-实体组合优先（动作/实体均去重）
    segs: list = []
    used_acts, used_ents = set(), set()
    for (act, ent), _ in pairs.most_common(12):
        if act in used_acts or ent in used_ents:
            continue
        if len(segs) >= 2:
            break
        segs.append(f"{ent}{act}")
        used_acts.add(act)
        used_ents.add(ent)

    # fallback：领域实体组合 → 动作词兜底 → 首句截断
    if not segs:
        segs = [w for w, _ in ent_freq.most_common(10)
                if w in domain_ents and w not in used_ents][:2]
    elif len(segs) == 1:
        extra = [w for w, _ in ent_freq.most_common(10)
                 if w in domain_ents and w not in used_ents]
        if extra:
            segs.append(extra[0])
    if not segs and act_freq:
        # 无实体时用高频动作词（测试/更换/焊接...）作标题元素
        segs = [a for a, _ in act_freq.most_common(2)]
    if not segs:
        # 终极兜底：取该章第一句前10字
        first = re.split(r'[。！？\n，]', text.strip())
        first = first[0].strip() if first else ''
        if first:
            segs = [first[:10] + ('…' if len(first) > 10 else '')]

    # 单实体 + 故障现象 → 补"故障"
    if len(segs) == 1 and not result_suffix:
        if any(fw in text for fw in _FAULT_WORDS):
            segs.append('故障')

    title = '与'.join(segs) if segs else ''
    if result_suffix and title:
        title = f"{title}·{result_suffix}"

    start_s = segments[s][0] // 1000
    ts = f"{start_s // 60:02d}:{start_s % 60:02d}"
    return f"[{ts}] {title}" if title else f"[{ts}]"


def _ms_to_ts(ms: int) -> str:
    """毫秒转 mm:ss（音频事件标记用）"""
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _split_by_boundaries(segments, scenes):
    """按转场边界(scenes, [ms,...])把 SRT segments 切成章节组 [[text,...],...]。"""
    bounds = sorted(b for b in (scenes or []) if b > 0)
    if not bounds:
        return []
    groups, cur = [], []
    bidx = 0
    for start, end, text in segments:
        while bidx < len(bounds) and start >= bounds[bidx]:
            if cur:
                groups.append(cur)
                cur = []
            bidx += 1
        cur.append(text)
    if cur:
        groups.append(cur)
    # 合并过碎的组（<12 句且非唯一）
    merged = []
    for g in groups:
        if merged and len(g) < 12:
            merged[-1].extend(g)
        else:
            merged.append(g)
    return merged


def _build_script_chapters(text: str, outdir: Path, scenes=None) -> List[Dict]:
    """构建"脚本化"章节结构：[{"title": 章节标题, "paragraphs": [段落...]}, ...]

    - 优先用 LLM 输出带章节小标题的 Markdown（解析成结构化数据）
    - 无 LLM 时用 SRT 时间间隔划分场景，标题为「场景 N」
    """
    # 1) LLM 优先：让模型直接给出带 ## 章节的完整脚本（仅当 --llm 显式开启时）
    llm_md = None
    if USE_LLM:
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

    # 2) 规则式：优先语义切分（词汇相似度+时间停顿，两级层次），无 SRT 时回退句数切分
    segments = _load_srt_segments(outdir)
    if segments:
        sem_chs = _semantic_chapters(segments)
        domain_ents = _domain_entity_set()
        chapters = []
        for ch in sem_chs:
            s, e = ch["range"]
            group = [t for _, _, t in segments[s:e]]
            # 中文用无空格连接；英文等拉丁文本用空格连接（避免 "day.Many" 粘连）
            sep = "" if _is_cjk_text(" ".join(group)) else " "
            paras, buf = [], []
            for sent in group:
                buf.append(_punctuate_sentence(sent))
                if len(buf) >= 4:          # 每 4 句一段
                    paras.append(sep.join(buf))
                    buf = []
            if buf:
                paras.append(sep.join(buf))
            if paras:
                ttl = _chapter_summary_title(segments, s, e, domain_ents)
                chapters.append({"title": ttl, "paragraphs": paras,
                                 "level": ch["level"]})
        if chapters:
            return chapters

    # 回退：无 SRT 时整篇按句数切分
    sents = [s.strip() for s in re.split(r'[。！？\n]', text) if s.strip()]
    groups = [sents[i:i + 10] for i in range(0, len(sents), 10)] or []

    chapters = []
    for i, group in enumerate(groups, 1):
        # 中文用无空格连接；英文等拉丁文本用空格连接（避免 "day.Many" 粘连）
        sep = "" if _is_cjk_text(" ".join(group)) else " "
        paras, buf = [], []
        for s in group:
            buf.append(_punctuate_sentence(s))
            if len(buf) >= 4:          # 每 4 句一段
                paras.append(sep.join(buf))
                buf = []
        if buf:
            paras.append(sep.join(buf))
        if paras:
            kw = _chapter_keyword("".join(group))
            ttl = f"场景 {i}" + (f" · {kw}" if kw else "")
            chapters.append({"title": ttl, "paragraphs": paras})
    return chapters


# 章节标题停用词：品牌名/通用词/描述词/拟声词/无意义高频词
_CHAPTER_STOPWORDS: frozenset = frozenset({
    # 品牌/产品名
    '戴尔', '三星', '外星人', '联想', '惠普', '华硕', '微星', '技嘉',
    '七彩虹', '影驰', '昂达', '铭瑄', '双敏', '艾尔莎', '耕升', '盈通',
    '斯巴达克', '磐正', '拯救者', '机械革命', '雷神', '神舟',
    'RTX', 'GTX', 'NVIDIA', 'AMD', 'Intel',
    # 通用代词/量词
    '东西', '这个', '那个', '一些', '什么', '怎么', '如何', '为什么',
    '哪里', '哪个', '哪些', '这么', '那么', '这些', '那些',
    '一个', '两个', '几个', '多少', '所有', '全部', '任何', '每个',
    '大家', '有人', '没人', '咱们', '你们', '他们', '我们',
    # 描述性副词/形容词
    '非常', '特别', '十分', '极其', '更加', '比较', '挺', '算',
    '明显', '清楚', '简单', '复杂', '重要', '关键', '主要', '次要',
    '可能', '大概', '或许', '也许', '一定', '肯定', '必须', '应该',
    '已经', '还在', '正在', '还是', '就是', '不过', '但是',
    '非常明显', '非常脆弱', '非常关键', '非常简单', '非常重要',
    '太贵', '太便宜', '太贵了', '非常好', '非常差',
    '不容易', '很容易', '不太行', '不太对',
    # 过于通用的名词
    '机器', '模式', '代表', '样子', '模样', '方式', '情况',
    '问题', '结果', '原因', '条件', '机会', '作用', '影响',
    '通讯', '交流', '沟通', '联系', '互动', '反馈', '信息',
    '工作', '任务', '项目', '计划', '方案', '措施',
    '时候', '时间', '地方', '过程', '方法',
    # 通用动词
    '测试', '检查', '确认', '发现', '开始', '结束', '完成', '继续',
    '知道', '觉得', '感觉', '认为', '希望', '需要', '想要', '打算',
    # 拟声词/语气词
    '滋滋', '嗡嗡', '哗哗', '咚咚', '砰砰', '咔咔', '嘀嘀', '啦啦',
    '嗯', '啊', '呢', '吧', '吗', '哦', '呀', '哇', '哎', '嘿',
    '妈', '妈呀', '哎呀', '天哪', '我的天', '个蛋', '白瞎',
    # 无意义高频词
    '全都', '都是', '都有', '那个那个', '这个这个', '就是就是',
    '线存', '包错', '搞的', '搞成像',
    '称荒', '称恐', '称荒称恐',
})

# 通用词黑名单：这些词在标题中永远没有意义
_CHAPTER_GENERIC_BLACKLIST: frozenset = frozenset({
    '机器', '设备', '系统', '功能', '性能', '配置',
    '型号', '版本', '更新', '升级', '下载', '安装',
    '文件', '文档', '程序', '软件', '应用',
    '页面', '网站', '平台', '服务', '数据',
    '内容', '信息', '消息', '通知', '提示',
    '设置', '选项', '菜单', '按钮', '链接',
    '用户', '客户', '朋友', '同学', '老师',
    '今天', '明天', '昨天', '现在', '以后', '以前',
    '之前', '之后', '刚才', '马上', '立刻',
    '这样', '那样', '这边', '那边', '这里', '那里',
    '自己', '别人', '大家', '所有人',
    '东西', '事物', '事情', '方面', '角度',
    '方法', '方式', '途径', '手段', '工具',
    '原因', '结果', '目的', '意义', '价值',
    '时间', '空间', '地方', '位置', '范围',
    '程度', '比例', '数量', '规模', '水平',
    '变化', '改变', '调整', '修改', '变更',
    '情况', '状况', '状态', '形势', '趋势',
    '问题', '困难', '挑战', '风险', '机遇',
    '条件', '环境', '背景', '因素', '要素',
    '环节', '步骤', '流程', '过程', '阶段', '时期',
    '方面', '角度', '层面', '维度', '方向',
    '模式', '类型', '类别', '种类', '品种',
    '代表', '象征', '标志', '特征', '特点',
    '意义', '目的', '目标', '宗旨', '原则',
    '要求', '标准', '规范', '规则', '制度',
    '政策', '方针', '策略', '方法', '措施',
    '效果', '作用', '影响', '结果', '后果',
    '原因', '起因', '缘由', '问题', '难题',
    '测试', '试验', '实验', '验证', '检验',
    '检查', '检测', '确认', '核实', '发现',
    '开始', '启动', '结束', '完成', '继续',
    '知道', '了解', '觉得', '感觉', '认为',
    '希望', '想要', '需要', '必须', '打算',
    '可以', '能够', '应该', '已经', '正在',
    '还是', '就是', '不过', '但是',
})


# 填充词：转写文本中的口语填充，清洗标题时去除
_FILLER_WORDS: frozenset = frozenset({
    '呃', '啊', '嗯', '哦', '唉', '哎', '嘿', '嗨',
    '那个', '这个', '就是', '然后', '其实', '其实呢',
    '对吧', '是吧', '好吧', '好啦', '好了',
    '你看', '你看啊', '你看这个', '你看那个',
    '我说', '你知道吗', '你知道吗', '知道吧',
    '反正', '反正呢', '其实吧', '说实话',
    '所以说', '所以说啊', '所以说呢',
    '怎么说呢', '怎么说呢', '怎么讲呢',
    '的话', '呢', '吧', '吗', '啊',
    '咱们', '咱们就', '咱们就', '咱们这',
    '那么', '那么呢', '那么这', '那么这',
    '这个呢', '那个呢', '这样呢', '那样呢',
})

# 代词/无意义开头：标题中不携带信息
_PRONOUN_PREFIXES: tuple = ('我', '你', '他', '她', '它', '我们', '你们', '他们', '咱们',
    '这个', '那个', '这些', '那些', '这样', '那样', '这么', '那么',
    '所以', '但是', '不过', '然后', '接着', '后来', '现在', '以后', '以前',
    '首先', '其次', '最后', '另外', '还有', '再说', '再说呢',
    '其实', '其实呢', '其实吧', '说实话', '说白了',
    '你看', '你看啊', '你看这个', '你看那个',
    '你看这', '你看那', '你看这', '你看那',
    '我觉得', '我感觉', '我认为', '我估计', '我估计呢',
    '说实话', '说白了', '说白了', '说白了吧',
)


def _clean_sentence(sent: str) -> str:
    """清洗句子：去除填充词、代词前缀、多余空格，返回干净的主题句。"""
    import re
    # 去除填充词
    for filler in _FILLER_WORDS:
        sent = sent.replace(filler, ' ')
    # 去除代词前缀（开头）
    for prefix in _PRONOUN_PREFIXES:
        if sent.startswith(prefix):
            sent = sent[len(prefix):]
    # 去除多余空格
    sent = re.sub(r'\s+', ' ', sent).strip()
    # 去除首尾标点
    sent = sent.strip('，。！？；：、,.!?;:')
    return sent


def _sentence_score(sent: str) -> float:
    """给句子打分：越短越好、名词越多越好、含领域词越好。"""
    if not sent:
        return 0.0
    score = 0.0
    # 长度分：8-20字最优，太短/太长扣分
    length = len(sent)
    if 8 <= length <= 20:
        score += 3.0
    elif 5 <= length < 8:
        score += 1.5
    elif 20 < length <= 30:
        score += 1.0
    elif length > 30:
        score += 0.3
    else:
        score += 0.5
    # 名词密度分：名词越多越 informative
    try:
        import jieba.posseg as pseg
        nouns = 0
        verbs = 0
        total = 0
        for w, flag in pseg.cut(sent):
            if len(w) >= 2:
                total += 1
                if flag in ('n', 'nr', 'ns', 'nt', 'nz', 'vn', 'an'):
                    nouns += 1
                elif flag in ('v', 'vd', 'vi'):
                    verbs += 1
        if total > 0:
            score += (nouns / total) * 4.0
            score += (verbs / total) * 2.0
    except Exception:
        pass
    # 领域词加分：包含硬件维修相关词汇
    domain_words = {'显卡', '显存', '主板', '散热', '硅脂', '风枪', 'BGA', '焊接',
        '花屏', '黑屏', '死机', '组装机', '二手', '笔记本', '维修', '板卡',
        '颗粒', '焊油', '虚焊', '芯片组', '供电', '插槽', '散热器', '核心',
        '内存', '板载', '硬盘', '屏幕', '摄像头', '电池', '驱动', '螺丝',
        '外壳', '标签', '翻新', '洋垃圾', '胶', '桥片', '导热',
        '报错', '通道', '颗粒', '焊台', '加热', '拆机', '检测',
        'CPU', 'GPU', 'BIOS', 'RTX', 'GTX', 'i9', 'i7',
    }
    for word in domain_words:
        if word in sent:
            score += 2.0
            break
    return score


def _chapter_keyword(text: str) -> str:
    """生成章节要点概述句作为标题。

    策略：
    1. 将章节文本按句号/问号/感叹号切分为句子
    2. 清洗每个句子（去除填充词、代词前缀）
    3. 给每个句子打分（长度、名词密度、领域词）
    4. 取最高分的句子作为标题
    5. 如果最高分句子太短（<6字），取第二高分的句子拼接
    6. 最终截取12-15字
    """
    import re
    # 按句号/问号/感叹号切分句子
    sentences = [s.strip() for s in re.split(r'[。！？\n]', text) if s.strip()]
    if not sentences:
        return ''

    # 清洗每个句子
    cleaned = []
    for sent in sentences:
        c = _clean_sentence(sent)
        if c and len(c) >= 4:  # 过滤太短的句子
            cleaned.append(c)
    if not cleaned:
        return ''

    # 给每个句子打分
    scored = [(sent, _sentence_score(sent)) for sent in cleaned]
    scored.sort(key=lambda x: -x[1])

    # 取最高分的句子
    best = scored[0][0]
    # 如果太短，尝试拼接第二高分的句子
    if len(best) < 6 and len(scored) > 1:
        best = best + ' ' + scored[1][0]

    # 截取12-15字
    if len(best) > 15:
        best = best[:15] + '…'
    elif len(best) > 12:
        best = best[:12] + '…'

    return best


def generate_markdown(text: str, title: str, outdir: Path, scenes=None, audio_events=None, ocr_results=None) -> Path:
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

    chapters = _build_script_chapters(text, outdir, scenes=scenes)
    if chapters:
        for ch in chapters:
            prefix = "##" if ch.get("level", 1) == 1 else "###"
            lines.append(f"{prefix} {ch['title']}")
            lines.append("")
            for para in ch["paragraphs"]:
                lines.append(para)
                # Bilingual: add Chinese translation after English
                if BILINGUAL_ENABLED and _is_english_text(para):
                    translated = _translate_text(para)
                    if translated != para:
                        lines.append(f"\n**中文翻译：** {translated}")
                lines.append("")
    else:
        # 兜底：用清洗后的段落文本
        for line in clean_transcript(text).split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
                # Bilingual: add Chinese translation after English
                if BILINGUAL_ENABLED and _is_english_text(line):
                    translated = _translate_text(line)
                    if translated != line:
                        lines.append(f"\n**中文翻译：** {translated}")
                lines.append("")

    # 音频事件标注追加到 Markdown 末尾
    if audio_events:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 音频事件标记")
        lines.append("")
        for s_ms, e_ms, tags in audio_events:
            lines.append(f"- `{_ms_to_ts(s_ms)}` {'、'.join('【' + t + '】' for t in tags)}")
        log(f"[音频] 追加 {len(audio_events)} 个片段标记到 Markdown")

    # OCR 结果追加到 Markdown 末尾
    if ocr_results:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 画面文字（OCR）")
        lines.append("")
        for ms, txt in ocr_results:
            lines.append(f"- `{_ms_to_ts(ms)}` {txt}")
        log(f"[OCR] 追加 {len(ocr_results)} 条画面文字到 Markdown")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    log(f"[Markdown] 已生成: {md_path}（{len(chapters)} 个章节）")

    return md_path


def _cjk_font_name() -> str:
    """按平台返回可用的中文字体名（优先思源黑体/Noto Sans SC，其次微软雅黑）。
    
    Windows 10+ 自带 Noto Sans SC（思源黑体），通过注册表检测。
    注册表里的 font name 形如 "Noto Sans SC (TrueType)"，取括号前部分即 "Noto Sans SC"。
    """
    import platform as _pf
    sysname = _pf.system().lower()
    if sysname == "windows":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
            ) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, _, _ = winreg.EnumValue(key, i)
                    n_lower = name.lower()
                    if "noto sans sc" in n_lower or "source han sans sc" in n_lower:
                        # "Noto Sans SC (TrueType)" → "Noto Sans SC"
                        return name.split("(")[0].strip()
        except Exception:
            pass
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


def generate_docx(text: str, title: str, outdir: Path, scenes: Optional[List] = None) -> Path:
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
    chapters = _build_script_chapters(text, outdir, scenes=scenes)

    if chapters:
        for ch in chapters:
            h = doc.add_heading(ch["title"],
                                level=2 if ch.get("level", 1) == 1 else 3)
            for r in h.runs:
                _set_run_font(r, cjk)
            for para_text in ch["paragraphs"]:
                p = doc.add_paragraph(para_text)
                _format_body_para(p, cjk)
                # Bilingual: add Chinese translation after English
                if BILINGUAL_ENABLED and _is_english_text(para_text):
                    translated = _translate_text(para_text)
                    if translated != para_text:
                        tp = doc.add_paragraph()
                        tr = tp.add_run(f"中文翻译：{translated}")
                        _set_run_font(tr, cjk)
                        r2 = tp.add_run()
                        r2.font.size = Pt(10)
                        r2.font.color.rgb = None  # gray
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

    if audio_events:
        lines.append("")
        lines.append("## 音频事件标记")
        lines.append("")
        for s_ms, e_ms, tags in audio_events:
            lines.append(f"- `{_ms_to_ts(s_ms)}` {'、'.join('【'+t+'】' for t in tags)}")

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
def _run_ocr(video_path, args):
    """统一的 OCR 调用入口：读 args 参数、识别、返回 [(ms, text), ...]。任何异常都降级为 []。

    门控：纯音频文件（mp3/wav/...，无视频流）直接跳过——没有画面可识别，
    强行跑只会浪费时间并返回 0 条结果。
    """
    if not getattr(args, "ocr", False):
        return []
    # 无画面则跳过（纯音频文件）
    try:
        if not scene_audio.has_video_stream(str(video_path)):
            log("[OCR] 输入为纯音频（无视频流），跳过画面文字识别")
            return []
    except Exception as e:
        log(f"[OCR] 画面探测失败({e})，按有画面继续")
    if not scene_audio.ocr_available():
        log("[OCR] PaddleOCR 不可用，跳过画面文字识别（安装：pip install paddleocr paddlepaddle）")
        return []
    try:
        res = scene_audio.ocr_video_frames(
            str(video_path),
            interval_sec=getattr(args, "ocr_interval", 2.0) or 2.0,
            crop_bottom=getattr(args, "ocr_bottom", None),
            max_frames=getattr(args, "ocr_max_frames", 150) or 150,
            diff_threshold=getattr(args, "ocr_diff", 2.5),
            max_side=getattr(args, "ocr_max_side", 960),
        )
        log(f"[OCR] 识别到 {len(res)} 条画面文字")
        return res
    except Exception as e:
        log(f"[OCR] 失败: {e}")
        return []


def _pre_translate_english(text: str):
    """Pre-translate all unique English paragraphs to warm the cache.
    Uses 0.5s inter-request delay to respect MyMemory free-tier rate limits.
    Without this delay, MyMemory blocks after ~3 rapid requests."""
    lines = text.split("\n")
    seen = set()
    to_translate = []
    for line in lines:
        s = line.strip()
        if s and _is_english_text(s) and s not in seen:
            seen.add(s)
            to_translate.append(s)
    if not to_translate:
        return
    log(f"[双语] 预翻译 {len(to_translate)} 条英文段落...")
    success, fail = 0, 0
    for i, para in enumerate(to_translate, 1):
        try:
            result = _translate_text(para, source_lang="auto", target_lang="zh-CN")
            if result != para:
                success += 1
                log(f"[双语]   [{i}/{len(to_translate)}] OK: {result[:40]}...")
            else:
                fail += 1
                log(f"[双语]   [{i}/{len(to_translate)}] 原样返回: {para[:40]}...")
        except Exception as e:
            fail += 1
            log(f"[双语]   [{i}/{len(to_translate)}] 失败: {e}")
        if i < len(to_translate):
            time.sleep(0.5)  # respect MyMemory rate limit
    log(f"[双语] 预翻译完成: {success} 成功, {fail} 失败, 缓存 {len(_TRANSLATE_CACHE)} 条")


def main():
    ap = argparse.ArgumentParser(description="微信媒体 + 多平台视频/音乐下载")
    ap.add_argument("input", help="视频链接、公众号文章链接、视频文件或音频文件路径")
    ap.add_argument("--type", choices=["video", "music"], default=None,
                    help="指定类型：video=视频处理, music=音乐处理（默认自动检测）")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--no-whisper", action="store_true", help="跳过转写")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium"],
                    help="Whisper模型: tiny(~150MB), base(~1GB), small(~2GB,默认), medium(~5GB)")
    ap.add_argument("--lang", default="zh",
                    help="语言: zh(中文), en(英文), auto(分块语种检测，中英混音视频用，较慢)")
    ap.add_argument("--denoise", action="store_true", help="启用降噪预处理（提升嘈杂环境识别率）")
    ap.add_argument("--enhance", action="store_true", help="启用场景/音频识别增强（镜头检测+人声分离+音频事件标签）")
    ap.add_argument("--ocr", action="store_true", help="启用视频帧OCR（识别画面中的字幕/歌词等文字，需安装 paddleocr）")
    ap.add_argument("--ocr-interval", type=float, default=2.0,
                    help="OCR 抽帧间隔（秒，默认2.0；越小越准但越慢）")
    ap.add_argument("--ocr-bottom", type=float, default=None,
                    help="只识别画面底部该比例区域（0.0~1.0，如 0.35），适合字幕/歌词，可减少干扰；默认全画面")
    ap.add_argument("--ocr-max-frames", type=int, default=150,
                    help="OCR 最大抽帧数（默认150，防止长视频跑飞）")
    ap.add_argument("--ocr-diff", type=float, default=2.5,
                    help="OCR 帧间差异阈值（默认2.5）。低于此值视为画面未变而跳过识别，"
                         "可省 60%%~85%% 的帧；设 0 关闭预筛（逐帧识别，最慢最全）")
    ap.add_argument("--ocr-max-side", type=int, default=960,
                    help="OCR 抽帧时限制长边像素（默认960，竖屏视频可大幅提速）；设0关闭缩放")
    ap.add_argument("--threads", type=int, default=None,
                    help="whisper 线程数（默认自动 min(8,CPU核数)；16核机器上 8 线程实测最优，"
                         "超过反而变慢）")
    ap.add_argument("--prompt", default=None,
                    help="whisper 初始提示词（人名/术语/歌名等，可提升专有名词识别准确率）")
    ap.add_argument("--terms", default=None,
                    help="术语词典 JSON 文件路径（ASR 同音/近音错误自动修正）。"
                         "默认使用内置金融词典 terminology/finance.json；"
                         "自定义路径如 --terms ~/.workbuddy/terminology/myterms.json")
    ap.add_argument("--ocr-correct", action="store_true",
                    help="启用 OCR 校正 ASR：用画面 OCR 文字替换 Whisper 转写错误片段，"
                         "仅当同时使用 --ocr 时有效（唱歌/歌词视频推荐开启）")
    ap.add_argument("--format", choices=["txt", "md", "docx", "all"], default="all",
                    help="输出格式: txt=纯文本, md=Markdown, docx=Word文档, all=全部(默认)")
    ap.add_argument("--breakdown", action="store_true", help="生成爆款拆解分析（结构化观点提炼）")
    ap.add_argument("--llm", action="store_true",
                    help="启用 LLM 智能整理（默认关闭，全程走本地规则式处理）。"
                         "需先配置 LLM_API_KEY/LLM_BASE_URL，或写入 ~/.config/multi-media-processor/llm.json")
    ap.add_argument("--bilingual", action="store_true",
                    help="启用双语转写：原文非中文部分保留，后面跟中文翻译。"
                         "需设置环境变量 BILINGUAL_ENABLED=1（默认关闭，避免网络依赖）")
    args = ap.parse_args()

    # LLM 总开关：默认关闭，仅当用户显式加 --llm 时启用
    global LLM_ENABLED, USE_LLM
    if args.llm:
        LLM_ENABLED = True
        USE_LLM = True
        if _load_llm_config():
            log("[LLM] 已按用户要求启用智能整理")
        else:
            log("[LLM] 已启用开关，但未检测到 API 配置（LLM_API_KEY 等），将回退本地规则式处理")
            LLM_ENABLED = False
            USE_LLM = False

    # Bilingual 总开关：默认关闭，仅当用户显式加 --bilingual 时启用
    global BILINGUAL_ENABLED
    if args.bilingual or os.environ.get("BILINGUAL_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        BILINGUAL_ENABLED = True
        log("[双语] 已启用双语转写模式")

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

        # 场景识别增强：转场边界切章 + 音频事件标签
        enhance = args.enhance
        scenes, audio_events = [], []
        if enhance:
            if scene_audio.scene_detect_available():
                scenes = scene_audio.detect_scenes(str(dest))
            if scene_audio.audio_tag_available():
                audio_events = scene_audio.classify_audio_events(str(dest))
        # 视频帧 OCR：识别画面中的字幕/歌词
        ocr_results = _run_ocr(dest, args)
        # Whisper 转写（用初始标题命名转写产物，避免与后续 {title}_raw.txt 重复）
        if not args.no_whisper:
            text = transcribe_whisper(dest, outdir, model=args.model, lang=args.lang,
                                      denoise=args.denoise, title=init_title,
                                      vocal_sep=enhance, threads=args.threads,
                                      prompt=args.prompt)
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

                # OCR 校正 ASR（唱歌视频专用）
                corrected_segments = None
                _srt_path = outdir / "subtitle.srt"
                if getattr(args, "ocr_correct", False) and ocr_results and _srt_path.exists():
                    try:
                        log("[OCR校正] 尝试用画面歌词校正 Whisper 转写...")
                        segments = scene_audio.parse_srt(str(_srt_path))
                        corrected_segments = scene_audio.align_ocr_to_srt(
                            segments, ocr_results,
                            tolerance_ms=2500, min_similarity=0.20
                        )
                        replaced = sum(1 for _s, _e, orig, cor in corrected_segments if orig != cor)
                        if replaced:
                            # 写回校正后 SRT（供下游生成）
                            corr_srt = scene_audio.generate_corrected_srt(corrected_segments, outdir, title)
                            log(f"[OCR校正] 替换了 {replaced} 个片段，已写入 {os.path.basename(corr_srt)}")
                            # 同时用校正文本更新 Markdown / DOCX 使用的 text
                            corrected_text = "\n".join(
                                cor for _s, _e, _orig, cor in corrected_segments
                            )
                            text = corrected_text
                        else:
                            log("[OCR校正] 未找到相似度达标的匹配，保留原转写")
                            corrected_segments = None
                    except Exception as e:
                        log(f"[OCR校正] 跳过: {e}")

                # 术语修正：应用 ASR 同音/近音错误词典
                text, term_count, term_domains = _apply_terminology(text, getattr(args, "terms", None))
                if term_count > 0:
                    # 写回修正后的 transcript_raw.txt
                    transcript_path = outdir / f"{title}_raw.txt"
                    if not transcript_path.exists():
                        transcript_path = outdir / "transcript_raw.txt"
                    transcript_path.write_text(text, encoding="utf-8")
                    domain_info = f" | 领域: {', '.join(term_domains)}" if term_domains else ""
                    log(f"[术语] 修正 {term_count} 处 ASR 同音/近音错误{domain_info}")

                # 生成 Markdown 和 DOCX
                if args.format in ("md", "all"):
                    generate_markdown(text, title, outdir, scenes=scenes, audio_events=audio_events, ocr_results=ocr_results)
                if args.format in ("docx", "all"):
                    generate_docx(text, title, outdir, scenes=scenes)
                if args.breakdown:
                    generate_breakdown(text, title, outdir)

                # 统一命名：视频/字幕也用主题名（须在 timestamped 生成之后）
                _unify_output_names(outdir, title, dest)
        else:
            # --no-whisper: 尝试读取已有的 transcript_raw.txt 继续生成
            _raw_files = list(outdir.glob("transcript_raw.txt"))
            if not _raw_files:
                _raw_files = list(outdir.glob("*_raw.txt"))
            if _raw_files:
                _raw_path = _raw_files[0]
                text = _raw_path.read_text(encoding="utf-8")
                title = _raw_path.stem.replace("_raw", "") or "transcript"
                simp = _to_simplified(text)
                if simp != text:
                    (outdir / f"{title}_simplified.txt").write_text(simp, encoding="utf-8")
                log(f"[复用] 使用已有转写: {_raw_path.name} ({len(text)} 字)")
                # 术语修正
                text, term_count, term_domains = _apply_terminology(text, getattr(args, "terms", None))
                if term_count > 0:
                    _raw_path.write_text(text, encoding="utf-8")
                    log(f"[术语] 修正 {term_count} 处 ASR 同音/近音错误" + (f" (领域: {', '.join(term_domains)})" if term_domains else ""))
                # Bilingual: pre-translate all English paragraphs (avoids rate-limit hangs mid-generation)
                if BILINGUAL_ENABLED:
                    _pre_translate_english(text)
                if args.format in ("md", "all"):
                    try:
                        generate_markdown(text, title, outdir, scenes=scenes, audio_events=audio_events, ocr_results=ocr_results)
                        log(f"[复用] Markdown 已生成")
                    except Exception as e:
                        log(f"[复用] Markdown 生成失败: {e}")
                if args.format in ("docx", "all"):
                    try:
                        generate_docx(text, title, outdir, scenes=scenes)
                        log(f"[复用] DOCX 已生成")
                    except Exception as e:
                        log(f"[复用] DOCX 生成失败: {e}")
                if args.breakdown:
                    generate_breakdown(text, title, outdir)
                _unify_output_names(outdir, title, dest)
            else:
                log("[复用] 未找到已有转写文件，跳过生成（建议先运行 Whisper 转写）")

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
        meta = process_sph(input_str, str(outdir), lang=args.lang,
                           model=args.model, enhance=args.enhance)
        log(f"输出目录: {outdir}")
        log(f"类型: {meta['type']}")
        for k, v in meta.get("files", {}).items():
            log(f"  - {k}: {v}")

        # 视频号分支的增强：镜头检测 + 音频事件 + 画面 OCR
        vf = Path(outdir) / "video.mp4"
        scenes, audio_events = [], []
        if args.enhance and vf.exists():
            if scene_audio.scene_detect_available():
                try:
                    scenes = scene_audio.detect_scenes(str(vf))
                    log(f"[增强] 镜头检测：{len(scenes)} 个转场")
                except Exception as e:
                    log(f"[增强] 镜头检测失败: {e}")
            if scene_audio.audio_tag_available():
                try:
                    audio_events = scene_audio.classify_audio_events(str(vf))
                    log(f"[增强] 音频事件：{len(audio_events)} 个片段")
                except Exception as e:
                    log(f"[增强] 音频事件失败: {e}")
        ocr_results = _run_ocr(vf, args) if vf.exists() else []

        # 读取已生成的转写文本（优先 raw，有中文才转简体）
        text = ""
        raw_path = outdir / "transcript_raw.txt"
        if raw_path.exists():
            raw_text = raw_path.read_text(encoding="utf-8")
            # 如果 raw 里有中文字符则转简体，否则保留原文（如英文视频）
            if any('\u4e00' <= c <= '\u9fff' for c in raw_text):
                text = _to_simplified(raw_text)
            else:
                text = raw_text
        # 术语修正
        if text:
            text, term_count, term_domains = _apply_terminology(text, getattr(args, "terms", None))
            if term_count > 0:
                raw_path.write_text(text, encoding="utf-8")
                log(f"[术语] 修正 {term_count} 处 ASR 同音/近音错误" + (f" (领域: {', '.join(term_domains)})" if term_domains else ""))
                # 同步修正 SRT 字幕（语义章节划分的正文来自 SRT，不修正会残留错误词）
                srt_path = outdir / "transcript_raw.srt"
                if srt_path.exists():
                    srt_text = srt_path.read_text(encoding="utf-8")
                    srt_fixed, srt_cnt, _ = _apply_terminology(srt_text, getattr(args, "terms", None))
                    if srt_cnt > 0:
                        srt_path.write_text(srt_fixed, encoding="utf-8")
                        log(f"[术语] SRT 字幕同步修正 {srt_cnt} 处")
        if text and args.format in ("md", "all"):
            title = safe_slug(meta.get("title", "视频号视频")) or "视频号视频"
            generate_markdown(text, title, outdir, scenes=scenes,
                              audio_events=audio_events, ocr_results=ocr_results)
        if text and args.format in ("docx", "all"):
            try:
                title = safe_slug(meta.get("title", "视频号视频")) or "视频号视频"
                generate_docx(text, title, outdir, scenes=scenes)
            except Exception as e:
                log(f"[DOCX] 生成失败: {e}")

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

        # 场景识别增强：转场边界切章 + 音频事件标签
        enhance = args.enhance
        scenes, audio_events = [], []
        if enhance:
            if scene_audio.scene_detect_available():
                scenes = scene_audio.detect_scenes(str(vf))
            if scene_audio.audio_tag_available():
                audio_events = scene_audio.classify_audio_events(str(vf))
        # 视频帧 OCR：识别画面中的字幕/歌词
        ocr_results = _run_ocr(vf, args)
        # 用初始标题命名转写产物（避免与后续 {title}_raw.txt 重复）
        text = transcribe_whisper(vf, outdir, model=args.model, lang=args.lang,
                                  denoise=args.denoise, title=init_title,
                                  vocal_sep=enhance, threads=args.threads,
                                  prompt=args.prompt)
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
                generate_markdown(text, title, outdir, scenes=scenes, audio_events=audio_events, ocr_results=ocr_results)

            # 生成 DOCX
            if args.format in ("docx", "all"):
                generate_docx(text, title, outdir, scenes=scenes)

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
