# -*- coding: utf-8 -*-
"""环境自检：Python 版本、依赖包、运行环境是否满足脚本要求。

什么时候跑：
    * 脚本第一次启动时（web_ui.main 里起一个后台线程自动跑一遍，
      结果同时进控制台日志和 Web 页面）；
    * Web 控制台「🧪 环境自检」卡片里的 [重新自检] 按钮（随时手动重跑）。

每一项给三种结论，页面/控制台上直接显示图标：
    ✅ ok    达标；
    ⚠️ warn  能跑，但与推荐值不一致（例如依赖版本和 requirements.txt 不同、
             炉石没开、日志目录里还没有 Power.log）；
    ❌ fail  必需项缺失/不满足，脚本很可能跑不起来或点了没反应，必须修。

为什么死磕 Python 3.12：OCR 推理后端 paddlepaddle 2.6.2 只有 cp312 及以下的
Windows 轮子（3.13+ 直接装不上），而本项目全部实测环境都是 3.12.14；
3.11 及以下虽然装得上，但与本项目验证过的环境不一致，容易出玄学问题。
所以这里把「3.12 + 64 位」作为唯一推荐值，其它版本一律 ❌ 并给出安装提示。

本模块只依赖标准库，导入它本身不会拉起 numpy/OCR 之类的重依赖
（缺依赖时更要能跑起来报错，否则自检就成了「没装依赖就跑不了自检」）。
"""
from __future__ import annotations

import importlib
import importlib.metadata
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent
REQUIREMENTS_PATH = ROOT / "requirements.txt"
UI_CONFIG_PATH = ROOT / "ui_config.json"

# ---------------------------------------------------------------- 结论常量
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_ICON = {STATUS_OK: "✅", STATUS_WARN: "⚠️", STATUS_FAIL: "❌"}

# ---------------------------------------------------------------- Python 版本
EXPECTED_PYTHON = (3, 12)
PYTHON_HINT = (
    "请装 Python 3.12（64 位）：conda create -n HTL python=3.12 -y && "
    "conda activate HTL，再执行 pip install -r requirements.txt。"
    "不要用 3.11 及以下，也不要用 3.13 及以上（OCR 依赖没有对应轮子）。")

# ---------------------------------------------------------------- 依赖清单
# 每项：imports=代码里真正 import 的名字；dist=PyPI 发行包名（可多个候选）；
#       why=这个包干什么用的；required=False 表示缺了只是功能受限、不算致命。
# 顺序按「装不上的话最先炸的排前面」，方便用户从上往下照着修。
DEPENDENCIES: tuple[dict, ...] = (
    {"imports": ("win32gui", "win32api", "win32ui", "pywintypes"),
     "dist": ("pywin32",), "why": "炉石窗口句柄 / 全屏截图 / 系统分辨率"},
    {"imports": ("cv2",), "dist": ("opencv-python-headless", "opencv-python"),
     "why": "截图裁剪、颜色判定、OCR 预处理"},
    {"imports": ("numpy",), "dist": ("numpy",), "why": "截图像素解析"},
    {"imports": ("PIL",), "dist": ("Pillow",), "why": "截图（ImageGrab）"},
    {"imports": ("paddleocr",), "dist": ("paddleocr",), "why": "盒子面板文字识别"},
    {"imports": ("paddle",), "dist": ("paddlepaddle",),
     "why": "OCR 推理后端（只有 Python 3.12 及以下有轮子）"},
    {"imports": ("psutil",), "dist": ("psutil",), "why": "炉石进程存活检测"},
    {"imports": ("keyboard",), "dist": ("keyboard",), "why": "Ctrl+Q 全局热键"},
    {"imports": ("pynput",), "dist": ("pynput",), "why": "鼠标移动与点击"},
    {"imports": ("requests",), "dist": ("Requests",), "why": "卡牌数据下载"},
    # click 只按发行包版本检查：仓库根目录自己的 click.py 会遮蔽 PyPI 的 click，
    # import click 找不到真正的第三方包（paddle/httpx 需要它，见 paddle_adapter）。
    {"imports": (), "dist": ("click",), "metadata_only": True,
     "why": "OCR 依赖链（paddle/httpx）用到的第三方 click"},
    {"imports": (), "dist": ("setuptools",), "metadata_only": True,
     "required": False, "why": "paddle 运行时用到的 pkg_resources"},
)

_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)")


# ---------------------------------------------------------------- 工具函数
def _normalize(name: str) -> str:
    """包名归一化：Requests / requests、opencv_python_headless / opencv-python-headless。"""
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


def _item(key: str, label: str, status: str, detail: str,
          hint: str = "", required: bool = True, purpose: str = "") -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail,
            "hint": hint, "required": bool(required), "purpose": purpose}


def _dist_version(dist_names) -> Optional[str]:
    """读取某个发行包的已安装版本号；读不到返回 None（不影响导入判定）。"""
    for name in dist_names:
        try:
            return importlib.metadata.version(name)
        except Exception:
            continue
    return None


def _requirements_pins(text: Optional[str] = None) -> dict[str, str]:
    """解析 requirements.txt 的 ``包名==版本`` 固定值，供版本比对用。

    解析不到（文件缺失/格式变了）就返回空字典，此时只检查能不能导入，
    不比对版本 —— 自检本身不能因为解析失败而报错。
    """
    if text is None:
        try:
            text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
        except Exception:
            return {}
    pins: dict[str, str] = {}
    for line in str(text).splitlines():
        match = _PIN_RE.match(line)
        if match:
            pins[_normalize(match.group(1))] = match.group(2)
    return pins


def _pinned_version(dist_names, pins: dict[str, str]) -> Optional[str]:
    for name in dist_names:
        pinned = pins.get(_normalize(name))
        if pinned:
            return pinned
    return None


# ---------------------------------------------------------------- 单项检查
def python_item(version: Optional[tuple] = None,
                bits: Optional[int] = None) -> dict:
    """Python 版本 + 位数检查（本项目只推荐 3.12 的 64 位解释器）。"""
    if version is None:
        version = (sys.version_info.major, sys.version_info.minor,
                   sys.version_info.micro)
    if bits is None:
        bits = 64 if sys.maxsize > 2 ** 32 else 32
    current = ".".join(str(int(v)) for v in version)
    detail = f"{current}（{bits} 位）"
    label = f"Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}（64 位）"

    if (int(version[0]), int(version[1])) != EXPECTED_PYTHON:
        return _item("python", label, STATUS_FAIL, detail, PYTHON_HINT)
    if int(bits) != 64:
        return _item("python", label, STATUS_FAIL, detail,
                     "32 位 Python 装不上 OCR 依赖（paddlepaddle 没有 32 位轮子），"
                     "请改装 64 位 Python 3.12。")
    return _item("python", label, STATUS_OK, detail)


def dependency_items(importer: Callable[[str], object] = importlib.import_module,
                     version_reader: Callable[[tuple], Optional[str]] = None,
                     pins: Optional[dict[str, str]] = None) -> list[dict]:
    """逐个 import 依赖包，并和 requirements.txt 的版本做比对。"""
    version_reader = version_reader or _dist_version
    pins = _requirements_pins() if pins is None else pins
    items: list[dict] = []
    for spec in DEPENDENCIES:
        imports = spec["imports"]
        dists = spec["dist"]
        required = bool(spec.get("required", True))
        metadata_only = bool(spec.get("metadata_only", False))
        # 只显示主发行包名（换行会很难看）；替代实现写在用途里。
        label = dists[0]
        purpose = str(spec.get("why", ""))
        if len(dists) > 1:
            purpose += f"（{'/'.join(dists[1:])} 亦可）"
        version = None
        try:
            version = version_reader(dists)
        except Exception:
            version = None
        if metadata_only:
            # 只认发行包元数据（例如仓库里的 click.py 会遮蔽第三方 click）。
            if not version:
                items.append(_item(
                    _normalize(dists[0]), label, STATUS_FAIL,
                    f"没装 {dists[0]}（找不到发行包元数据）",
                    ("pip install -r requirements.txt" if required else
                     f"可选依赖，需要的话 pip install {dists[0]}"), required,
                    purpose))
            elif (pinned := _pinned_version(dists, pins)) and str(version) != str(pinned):
                items.append(_item(
                    _normalize(dists[0]), label, STATUS_WARN,
                    f"已安装 {version}，requirements.txt 要求 {pinned}",
                    f"pip install {dists[0]}=={pinned}", required, purpose))
            else:
                items.append(_item(_normalize(dists[0]), label, STATUS_OK,
                                   f"已安装 {version}", "", required, purpose))
            continue

        failed_module = None
        error = None
        for module_name in imports:
            try:
                importer(module_name)
            except Exception as exc:      # 缺包、DLL 加载失败、版本不兼容都算
                failed_module = module_name
                error = exc
                break
        if failed_module is not None:
            hint = (f"pip install -r requirements.txt"
                    if required else
                    f"可选依赖，缺了不影响出牌；需要的话 pip install {dists[0]}")
            items.append(_item(
                _normalize(dists[0]), label, STATUS_FAIL,
                f"导入 {failed_module} 失败：{type(error).__name__}: {error}",
                hint, required, purpose))
            continue

        pinned = _pinned_version(dists, pins)
        if not version:
            items.append(_item(
                _normalize(dists[0]), label, STATUS_WARN,
                "已安装（读不到版本号）", "", required, purpose))
        elif pinned and str(version) != str(pinned):
            items.append(_item(
                _normalize(dists[0]), label, STATUS_WARN,
                f"已安装 {version}，requirements.txt 要求 {pinned}",
                f"如需与实测环境一致：pip install {dists[0]}=={pinned}", required,
                purpose))
        else:
            items.append(_item(
                _normalize(dists[0]), label, STATUS_OK,
                f"已安装 {version}", "", required, purpose))
    return items


# ---------------------------------------------------------------- 运行环境
def _config_handle():
    """拿一个 RecommendationConfig 实例（读用户的 ui_config.json 覆盖）。"""
    try:
        from config import RecommendationConfig
        return RecommendationConfig()
    except Exception:
        return None


def _expected_desktop_size(config) -> tuple[int, int]:
    size = getattr(config, "desktop_size", None)
    if isinstance(size, (tuple, list)) and len(size) == 2:
        return int(size[0]), int(size[1])
    return (1920, 1080)


def _expected_dpi(config) -> int:
    try:
        return int(getattr(config, "desktop_dpi", 96))
    except Exception:
        return 96


def _current_screen_size() -> tuple[int, int]:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return (0, 0)


def _current_dpi() -> int:
    try:
        import ctypes
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 0


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _load_ui_config() -> dict:
    try:
        import json
        data = json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ocr_model_item() -> dict:
    """OCR 模型目录检查（缺了首次识别会自动下载，所以只算提示）。"""
    purpose = "盒子面板文字识别用的 ppocrv4 模型"
    try:
        from config import OCR_MODEL_ROOT
        root = Path(OCR_MODEL_ROOT)
    except Exception:
        return _item("ocr-model", "OCR 模型目录", STATUS_WARN,
                     "无法解析模型目录配置", "", False, purpose)
    if not root.is_dir():
        return _item("ocr-model", "OCR 模型目录", STATUS_WARN,
                     f"还没下载：{root}",
                     "第一次识别时会自动下载；也可用环境变量 "
                     "HS_OCR_MODEL_ROOT 指向已有模型目录。", False, purpose)
    models = [p.name for p in root.iterdir() if p.is_dir()]
    if not models:
        return _item("ocr-model", "OCR 模型目录", STATUS_WARN,
                     f"{root} 里没有模型子目录",
                     "第一次识别时会自动下载模型。", False, purpose)
    return _item("ocr-model", "OCR 模型目录", STATUS_OK,
                 f"{root}（{len(models)} 个模型）", "", False, purpose)


def _log_dir_item() -> dict:
    """炉石日志目录 + Power.log（脚本靠它判断回合，必须有）。"""
    purpose = "判断回合/出牌的依据（Power.log）"
    cfg = _load_ui_config()
    raw = str(cfg.get("log_root") or "").strip()
    if not raw:
        return _item("log-root", "炉石日志目录", STATUS_FAIL, "还没填写",
                     "在「👤 基础配置」里填 Logs 文件夹，例如 "
                     "D:\\Battle.net\\Hearthstone\\Logs。", True, purpose)
    path = Path(raw)
    if not path.is_dir():
        return _item("log-root", "炉石日志目录", STATUS_FAIL,
                     f"目录不存在：{raw}", "检查路径是否写错（游戏更新后可能变）。",
                     True, purpose)
    try:
        from power_log import find_latest_power_log
        power_log = find_latest_power_log(path)
    except Exception:
        power_log = None
    if power_log is None:
        return _item("log-root", "炉石日志目录", STATUS_WARN,
                     f"{raw}（暂时没有 Power.log）",
                     "目录没问题，打一局对战后会自动生成 Power.log。", True, purpose)
    return _item("log-root", "炉石日志目录", STATUS_OK, f"{raw}", "", True, purpose)


def _hearthstone_item() -> dict:
    purpose = "窗口句柄与截屏对象"
    try:
        from get_screen import get_HS_hwnd
        hwnd = int(get_HS_hwnd())
    except Exception:
        hwnd = 0
    if hwnd:
        return _item("hearthstone", "炉石窗口", STATUS_OK, "已找到炉石窗口", "",
                     False, purpose)
    return _item("hearthstone", "炉石窗口", STATUS_WARN, "现在没找到炉石窗口",
                 "没开也没关系：点「开始运行」会先拉起战网和炉石。", False, purpose)


def _client_mapping_items(config) -> list[dict]:
    """客户区几何检查（**不再检测绝对分辨率**）。

    坐标换算层（display_mapping）会跟着客户区尺寸与窗口屏幕位置走，所以
    "屏幕是不是 1920×1080"已经不是判据——4K、2K、1080p 都能跑。这里只做：

    * 客户区信息（永远只展示，不拦）；
    * 客户区比例（非 16:9 才 FAIL：等比换算会把画面横向拉偏）；
    * 客户区尺寸（小到装不下固定像素的盒子面板才 FAIL）；
    * 炉石被遮挡（WARN：坐标不受影响，但截屏内容不是炉石）。

    炉石没运行时全部退化为提示，不阻塞（没开游戏本来就该能过自检）。
    """
    purpose = "客户区几何决定点击/截图的坐标换算"
    try:
        import display_mapping
    except Exception:
        return []

    try:
        mapping = display_mapping.display_mapping()
    except Exception:
        mapping = None

    items: list[dict] = []

    # 窗口位置实时跟踪：**无论炉石有没有运行都展示**（窗口最小化/没开时正是
    # 需要确认"跟踪到底在不在工作"的时候）。
    try:
        from display_mapping import tracker_stats
        info = tracker_stats()
        items.append(_item("window-tracker", "窗口位置跟踪", STATUS_OK,
                           info.get("text", ""), "", False,
                           "窗口移动后点击/截图坐标能否立刻跟着走"))
    except Exception:
        pass

    if mapping is None:
        width, height = _current_screen_size()
        screen_text = (f"{width}×{height}" if width and height else "读取失败")
        items.append(_item(
            "client", "炉石客户区", STATUS_WARN, f"读不到（炉石未运行）；屏幕 {screen_text}",
            "坐标换算依赖客户区几何；启动炉石后重跑自检即可看到实际客户区。",
            False, "窗口不在屏幕原点时按客户区换算点击/截图坐标"))
        return items

    items.append(_item("client", "炉石客户区", STATUS_OK, mapping.debug_text(),
                       "", False, purpose))

    if not mapping.compatible():
        items.append(_item(
            "client-aspect", "客户区比例", STATUS_FAIL,
            f"{mapping.width}×{mapping.height} 不是 16:9",
            "坐标按比例换算要求客户区接近 16:9（基准 1920×1080）："
            "请用全屏或 16:9 的窗口尺寸，否则点击会横向拉偏。",
            True, purpose))
    if mapping.too_small():
        items.append(_item(
            "client-size", "客户区尺寸", STATUS_FAIL,
            f"{mapping.width}×{mapping.height} 太小",
            "盒子面板是固定像素的独立窗口，炉石窗口太小会把它挤出屏幕："
            "请把窗口调大（客户区至少 960×540）。", True, purpose))

    try:
        from get_screen import get_HS_hwnd, is_allowed_hearthstone_foreground
        hwnd = int(get_HS_hwnd())
        if hwnd and not is_allowed_hearthstone_foreground(hwnd):
            items.append(_item(
                "foreground", "炉石窗口在前台", STATUS_WARN, "被其它窗口遮挡",
                "坐标换算与遮挡无关，仍会点对位置；但截图/OCR 会拍到遮挡窗口，"
                "运行时请把炉石留在最前面。", False, "截图区域是否拍到炉石画面"))
    except Exception:
        pass
    return items


def environment_items(config=None) -> list[dict]:
    """管理员权限 / 客户区几何（含缩放）/ 炉石窗口 / 日志目录 / OCR 模型。"""
    config = config if config is not None else _config_handle()

    if _is_admin():
        admin = _item("admin", "管理员权限", STATUS_OK, "已以管理员身份运行",
                      "", True, "鼠标/键盘点击能否生效的前提")
    else:
        admin = _item(
            "admin", "管理员权限", STATUS_FAIL, "当前不是管理员",
            "鼠标/键盘操作会被系统拦掉（点了没反应）。"
            "请以管理员身份运行：右键命令提示符 → 以管理员身份运行 → python web_ui.py。",
            True, "鼠标/键盘点击能否生效的前提")

    expected_dpi = _expected_dpi(config)
    dpi = _current_dpi()
    purpose = "缩放不是 100% 时物理像素与逻辑像素会分裂"
    if dpi == 0:
        scaling = _item("dpi", "显示缩放（DPI）", STATUS_WARN, "读取失败", "",
                        False, purpose)
    elif dpi == expected_dpi:
        scaling = _item("dpi", "显示缩放（DPI）", STATUS_OK,
                        f"{dpi}（{round(dpi / 96 * 100)}%）", "", False, purpose)
    else:
        # 不再当致命错误：换算是按物理像素做的，这里只提醒可能引起坐标分裂。
        scaling = _item(
            "dpi", "显示缩放（DPI）", STATUS_WARN,
            f"{dpi}（{round(dpi / 96 * 100)}%，建议 100%）",
            "显示设置 → 缩放建议改成 100%：不是 100% 时物理像素与逻辑像素会分裂，"
            "截图与点击可能对不上。", False, purpose)

    return ([admin] + _client_mapping_items(config) + [scaling]
            + [_hearthstone_item(), _log_dir_item(), _ocr_model_item()])


# ---------------------------------------------------------------- 总入口
def run_checks(importer: Optional[Callable[[str], object]] = None,
               version_reader: Optional[Callable] = None,
               environment_probe: Optional[Callable[[], list]] = None,
               pins: Optional[dict[str, str]] = None,
               version: Optional[tuple] = None,
               bits: Optional[int] = None) -> dict:
    """跑完整自检，返回可直接 JSON 序列化的结果。

    参数都是给测试用的注入口：默认真实 import、真实读包版本、真实探测环境。
    任何一项探测抛异常都不会让整个自检挂掉（缺依赖时更要能给出结论）。
    """
    items = [python_item(version=version, bits=bits)]
    items.extend(dependency_items(
        importer or importlib.import_module, version_reader, pins))

    if environment_probe is None:
        probe = environment_items
    else:
        probe = environment_probe
    try:
        items.extend(list(probe() or []))
    except Exception as exc:
        items.append(_item("environment", "运行环境探测", STATUS_WARN,
                           f"探测失败：{type(exc).__name__}: {exc}", "", False))

    failed = [i for i in items
              if i["status"] == STATUS_FAIL and i.get("required", True)]
    warned = [i for i in items if i["status"] == STATUS_WARN]
    passed = [i for i in items if i["status"] == STATUS_OK]

    if failed:
        names = "、".join(i["label"] for i in failed)
        summary = f"自检未通过：{len(failed)} 项必须修复（{names}）"
    elif warned:
        summary = f"自检通过（{len(passed)} 项达标，{len(warned)} 项提示）"
    else:
        summary = f"自检通过：{len(passed)} 项全部达标"

    return {
        "ok": not failed,
        "summary": summary,
        "failed": len(failed),
        "warned": len(warned),
        "passed": len(passed),
        "total": len(items),
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": f"{platform.system()} {platform.release()}",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }


def format_report(result: dict) -> str:
    """把自检结果排版成控制台日志文本。"""
    lines = [f"[自检] Python {result.get('python', '?')} · {result.get('platform', '')}",
             f"[自检] {result.get('summary', '')}"]
    for item in result.get("items", []):
        icon = STATUS_ICON.get(item.get("status"), "?")
        line = f"[自检] {icon} {item.get('label', '')}：{item.get('detail', '')}"
        hint = item.get("hint") or ""
        if hint and item.get("status") != STATUS_OK:
            line += f" —— {hint}"
        lines.append(line)
    return "\n".join(lines)


def main() -> int:
    """命令行直接跑：python selfcheck.py（无参数，退出码 0=通过 1=有问题）。"""
    result = run_checks()
    print(format_report(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
