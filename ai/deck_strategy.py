# 卡组策略自动发现：每个 ai/ 子文件夹就是一个卡组，网页下拉框直接由文件夹名生成。
#
# 目录约定（新增卡组只需要新建文件夹，**不需要改任何代码**）：
#   ai/<卡组名>/
#       <核心文件>.py     文件名不含 “_” 的 .py（如 kuaimu.py），定义策略类
#       <辅助文件>.py     可选的辅助模块（如 kuaimu_context.py）
#       <说明>.txt        打法思路/留牌策略（可选）
#
# 自动排除的目录：outofdate、__pycache__、以 “_” 或 “.” 开头的隐藏目录；
# 没有核心 .py（或核心 .py 是空文件）的目录也暂不出现，等写完策略自动生效。
# 文件夹名既是 ui_config.json 里 strategy 的取值，也是网页下拉框的显示名。
#
# 策略类约定（与搜索/留牌流程对接的鸭子类型接口）：
#   rankState(state, actions)   必需：场面 + 动作序列评分（见 search.rankState）
#   choose(state)               可选：换牌阶段留牌；缺省 = 全部保留
#   build_deck_context(state)   可选：本局卡组上下文（任务进度等）
#
# 加载方式：文件夹被注册成包 `ai.<卡组名>`，核心文件按真实路径加载，
# 因此核心文件里写 `from .辅助模块 import ...` 或
# `from ai.<卡组名>.辅助模块 import ...` 都能正常工作。
from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import sys
import threading
import time
from pathlib import Path

import config

# ai/ 目录（本文件所在目录）。
AI_DIR = Path(__file__).resolve().parent
# 不是卡组策略的目录。
EXCLUDED_FOLDERS = frozenset({"outofdate", "__pycache__"})
# 目录扫描缓存：搜索每展开一个节点都会问一次策略 key，不能每次都扫盘。
# 5 秒后自动重新扫描，新建/删除文件夹无需重启 Web。
_SCAN_TTL = 5.0

_scan_lock = threading.Lock()
_scan_cache = {"at": 0.0, "folders": []}

# 已加载的策略类：卡组名 -> (上次校验时间, 文件夹 .py 指纹, 类)。
# 评分在搜索里被高频调用，mtime 校验按 _SCAN_TTL 节流，避免每个节点都 stat 磁盘。
_CLASSES: dict[str, tuple[float, object, type]] = {}
# 加载失败的卡组：卡组名 -> (上次校验时间, 文件夹 .py 指纹, 错误信息)
_FAILURES: dict[str, tuple[float, object, str]] = {}
_load_lock = threading.Lock()


# ---------------------------------------------------------------- 目录发现
def core_file(folder: Path) -> Path | None:
    """返回文件夹里的核心策略文件：文件名不含 “_” 且有内容的 .py。

    有多个候选时优先取与文件夹同名的（ai/任务牧/任务牧.py），否则取排序后的第一个。
    空文件（还没写策略）不算核心文件，对应文件夹暂不出现在下拉框里。
    """
    try:
        candidates = sorted(path for path in folder.glob("*.py")
                            if "_" not in path.stem and path.stat().st_size > 0)
    except OSError:
        return None
    if not candidates:
        return None
    same_name = [path for path in candidates if path.stem == folder.name]
    return (same_name or candidates)[0]


def _scan_folders() -> list[str]:
    try:
        entries = [path for path in AI_DIR.iterdir()
                   if path.is_dir()
                   and path.name not in EXCLUDED_FOLDERS
                   and not path.name.startswith(("_", "."))]
    except OSError:
        return []
    return sorted(path.name for path in entries
                  if core_file(path) is not None)


def strategy_folders(refresh: bool = False) -> list[str]:
    """ai/ 下所有有效卡组文件夹名（含核心 .py 的目录），按名称排序。"""
    now = time.monotonic()
    with _scan_lock:
        fresh = (not refresh and _scan_cache["folders"]
                 and now - _scan_cache["at"] < _SCAN_TTL)
        if fresh:
            return list(_scan_cache["folders"])
        names = _scan_folders()
        _scan_cache["folders"] = names
        _scan_cache["at"] = now
        return list(names)


def strategy_labels() -> dict:
    """网页下拉框的卡组选项：文件夹名 -> 显示名（文件夹名本身）。"""
    return {name: name for name in strategy_folders()}


# ---------------------------------------------------------------- 模块加载
def _ensure_package(name: str) -> None:
    """把 ai/<卡组名> 注册成包，让策略文件里的相对导入可用。"""
    package_name = f"ai.{name}"
    if package_name in sys.modules:
        return
    spec = importlib.machinery.ModuleSpec(package_name, None, is_package=True)
    package = importlib.util.module_from_spec(spec)
    package.__path__ = [str(AI_DIR / name)]
    sys.modules[package_name] = package
    parent = sys.modules.get("ai")
    if parent is not None:
        setattr(parent, name, package)


def _pick_strategy_class(module, module_name: str, stem: str) -> type | None:
    """在核心文件里找出策略类：优先有 choose()，其次类名与文件名一致。"""
    classes = [obj for _, obj in inspect.getmembers(module, inspect.isclass)
               if obj.__module__ == module_name
               and hasattr(obj, "rankState")]
    if not classes:
        return None
    classes.sort(key=lambda cls: (
        not hasattr(cls, "choose"),
        cls.__name__.lower() != stem.lower(),
        cls.__name__,
    ))
    return classes[0]


def _folder_fingerprint(path: Path):
    """卡组的「版本号」= 文件夹里每个 .py 的 (名字, mtime, 大小)。

    只盯核心文件会漏掉辅助模块。2026-10-26 02:11 核心文件 dwys.py 已经
    `from .dwys_context import CARD_ID_CUTHUN`，而 dwys_context.py 还没写完，
    加载失败被按核心文件 mtime 缓存住；之后补全的是辅助模块、核心文件 mtime
    没变，于是整局再也不重试，AI 每个回合都只能「结束回合」（连输 20+ 局）。
    """
    entries = []
    for source in path.parent.glob("*.py"):
        try:
            stat = source.stat()
        except OSError:
            continue
        entries.append((source.name, stat.st_mtime_ns, stat.st_size))
    entries.sort()
    return (path.name, tuple(entries))


def _purge_modules(name: str) -> None:
    """清掉这个卡组包下所有已加载的子模块（核心文件 + 辅助模块）。

    只 pop 核心模块不够：辅助模块还留在 sys.modules 里，重试时拿到的仍是旧
    代码。上面那次失败后 dwys_context 就一直留在 sys.modules，即使文件补全，
    `from .dwys_context import CARD_ID_CUTHUN` 也永远失败。
    """
    prefix = f"ai.{name}."
    for module_name in [key for key in sys.modules if key.startswith(prefix)]:
        sys.modules.pop(module_name, None)


def _load_class(name: str, path: Path) -> type:
    _ensure_package(name)
    _purge_modules(name)
    module_name = f"ai.{name}.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)   # 失败别留半成品模块，下次可重试
        raise
    strategy_class = _pick_strategy_class(module, module_name, path.stem)
    if strategy_class is None:
        raise ImportError(f"{path.name} 里没有找到带 rankState() 的策略类")
    return strategy_class


def _class_for(key: str) -> type | None:
    """按卡组名加载策略类；文件夹里任一 .py 改动后自动重载，失败打印一次。"""
    path = core_file(AI_DIR / key)
    if path is None:
        return None
    now = time.monotonic()
    with _load_lock:
        cached = _CLASSES.get(key)
        if cached is not None and now - cached[0] < _SCAN_TTL:
            return cached[2]
        failed = _FAILURES.get(key)
        if failed is not None and now - failed[0] < _SCAN_TTL:
            return None
        try:
            mtime = _folder_fingerprint(path)
        except OSError:
            return None
        if cached is not None and cached[1] == mtime:
            _CLASSES[key] = (now, mtime, cached[2])   # 文件没变，只刷新校验时间
            return cached[2]
        if failed is not None and failed[1] == mtime:
            _FAILURES[key] = (now, mtime, failed[2])   # 还是坏的，别重复刷日志
            return None
        try:
            strategy_class = _load_class(key, path)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            _FAILURES[key] = (now, mtime, message)
            print(f"[策略] 加载「{key}」失败，该卡组暂不可用：{message}")
            return None
        _CLASSES[key] = (now, mtime, strategy_class)
        _FAILURES.pop(key, None)
        return strategy_class


# ---------------------------------------------------------------- 对外接口
# 策略实例缓存：策略类现在都是纯静态方法（无状态），缓存一次反复用；
# 以后某个卡组要在实例里记东西（比如“这张牌已经打过了”）也能直接生效。
_INSTANCES: dict = {}


def deck_strategy_key(key: str = None):
    """返回有效的卡组策略 key；选的是盒子AI/未知名字时返回 None。"""
    if key is None:
        key = config.strategy_setting()
    return key if key in strategy_folders() else None


def deck_strategy(key: str = None):
    """取当前（或指定）卡组策略的实例；盒子AI/未知/加载失败返回 None。"""
    key = deck_strategy_key(key)
    if key is None:
        return None
    strategy_class = _class_for(key)
    if strategy_class is None:
        return None
    instance = _INSTANCES.get(key)
    if not isinstance(instance, strategy_class):
        instance = strategy_class()
        _INSTANCES[key] = instance
    return instance


def strategy_class(key: str = None):
    """取当前（或指定）卡组策略的类（测试/自检用）；没有则返回 None。"""
    key = deck_strategy_key(key)
    return _class_for(key) if key is not None else None
