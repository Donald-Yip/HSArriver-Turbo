"""Read-only native readiness probes for the explicit one-game live gate."""

from __future__ import annotations

import ctypes
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from hslegend.domain.primitives import PrimitiveMixin

from .factory import _load_legacy_runtime


SAFE_START_PHASES = frozenset({"main_menu", "deck_select"})
_PLACEHOLDER_NAMES = frozenset({"", "YOURNAME#1234", "CHANGETHIS#54321"})
_PROCESS_NAMES = {"hearthstone": "hearthstone.exe", "hsang": "hsang.exe"}


@dataclass(frozen=True, slots=True)
class PreflightCheck(PrimitiveMixin):
    """One ordered readiness decision with stable failure semantics."""

    check_id: str
    ok: bool
    reason_code: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.check_id, str) or not self.check_id.strip():
            raise ValueError("check_id must be non-empty text")
        if type(self.ok) is not bool:
            raise TypeError("ok must be a bool")
        if not self.ok and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("failed check requires a reason_code")


@dataclass(frozen=True, slots=True)
class PreflightReport(PrimitiveMixin):
    """Stable machine-readable readiness report; never performs a game action."""

    ok: bool
    deck_index: int
    ui_phase: str
    checks: tuple[PreflightCheck, ...]
    live_command: str

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("ok must be a bool")
        if type(self.deck_index) is not int or self.deck_index < 0:
            raise ValueError("deck_index must be a non-negative int")
        checks = tuple(self.checks)
        if not checks or any(not isinstance(item, PreflightCheck) for item in checks):
            raise ValueError("checks must contain at least one PreflightCheck")
        if self.ok != all(item.ok for item in checks):
            raise ValueError("report ok must equal the conjunction of its checks")
        if not isinstance(self.ui_phase, str) or not self.ui_phase.strip():
            raise ValueError("ui_phase must be non-empty text")
        if not isinstance(self.live_command, str) or not self.live_command.strip():
            raise ValueError("live_command must be non-empty text")
        object.__setattr__(self, "checks", checks)


@dataclass(frozen=True, slots=True)
class PreflightProbes:
    """Injected read-only probes used by the preflight evaluator."""

    runtime: Callable[[], str | Path]
    identity: Callable[[], tuple[str, str]]
    power_log: Callable[[str], str | Path | None]
    processes: Callable[[], Iterable[str]]
    ui: Callable[[], Mapping[str, Any]]
    is_admin: Callable[[], bool]
    deck_slot: Callable[[int], tuple[int, int]]

    def __post_init__(self) -> None:
        for name in (
            "runtime",
            "identity",
            "power_log",
            "processes",
            "ui",
            "is_admin",
            "deck_slot",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} must be callable")


def _check(
    check_id: str,
    ok: bool,
    reason_code: str = "",
    detail: str = "",
    *,
    success_detail: str = "",
) -> PreflightCheck:
    return PreflightCheck(
        check_id=check_id,
        ok=ok,
        reason_code="" if ok else reason_code,
        detail=str(success_detail if ok else detail)[:300],
    )


def _safe_detail(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:300]


def _default_live_command(deck_index: int) -> str:
    return (
        "uv run hslegend-onegame --allow-live "
        f"--deck-index {deck_index} --max-turns 20 --timeout-seconds 900 --json"
    )


def _report(
    deck_index: int,
    checks: list[PreflightCheck],
    phase: str = "unknown",
    *,
    live_command: str,
) -> PreflightReport:
    return PreflightReport(
        ok=all(item.ok for item in checks),
        deck_index=deck_index,
        ui_phase=phase,
        checks=tuple(checks),
        live_command=live_command,
    )


def _process_names(values: Iterable[str]) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("process probe must return an iterable of names")
    names: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("process names must be text")
        if value.strip():
            names.add(Path(value).name.lower())
    return names


def _path_text(value: str | Path, field_name: str) -> str:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{field_name} must be a string or Path")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def run_preflight(
    deck_index: int,
    probes: PreflightProbes,
    *,
    live_command: str | None = None,
) -> PreflightReport:
    """Evaluate readiness from injected probes without side effects."""

    if not isinstance(probes, PreflightProbes):
        raise TypeError("probes must be PreflightProbes")
    if type(deck_index) is not int or deck_index < 0:
        raise ValueError("deck_index must be a non-negative int")
    command = live_command or _default_live_command(deck_index)
    if not isinstance(command, str) or not command.strip():
        raise ValueError("live_command must be non-empty text")

    checks: list[PreflightCheck] = []
    try:
        root = _path_text(probes.runtime(), "runtime root")
    except Exception as exc:
        checks.append(
            _check(
                "legacy_runtime",
                False,
                "legacy_runtime_unavailable",
                _safe_detail(exc),
            )
        )
        return _report(deck_index, checks, live_command=command)
    checks.append(_check("legacy_runtime", True, success_detail=root))

    user_name = ""
    log_root = ""
    log_root_ok = False
    config_probe_error = ""
    try:
        identity = probes.identity()
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or not all(isinstance(value, str) for value in identity)
        ):
            raise TypeError("identity probe must return two strings")
        user_name, log_root = identity
    except Exception as exc:
        config_probe_error = _safe_detail(exc)
        checks.append(
            _check("config_identity", False, "config_probe_error", config_probe_error)
        )
        checks.append(_check("log_root", False, "config_probe_error", config_probe_error))
    else:
        normalized_name = user_name.strip()
        checks.append(
            _check(
                "config_identity",
                normalized_name.upper() not in _PLACEHOLDER_NAMES,
                "config_identity_missing",
                "configure the Hearthstone BattleTag before a live run",
                success_detail="configured",
            )
        )
        try:
            log_root_ok = bool(log_root) and Path(log_root).expanduser().is_dir()
        except (OSError, ValueError):
            log_root_ok = False
        checks.append(
            _check(
                "log_root",
                log_root_ok,
                "log_root_missing",
                f"configured log root is not a directory: {log_root!r}",
                success_detail=log_root,
            )
        )

    if config_probe_error:
        checks.append(
            _check("power_log", False, "config_probe_error", config_probe_error)
        )
    elif not log_root_ok:
        checks.append(
            _check(
                "power_log",
                False,
                "log_root_missing",
                "Power.log cannot be discovered without an existing log root",
            )
        )
    else:
        try:
            raw_power_log = probes.power_log(log_root)
            power_log_path = (
                _path_text(raw_power_log, "Power.log path")
                if raw_power_log is not None
                else ""
            )
        except Exception as exc:
            checks.append(
                _check("power_log", False, "power_log_probe_error", _safe_detail(exc))
            )
        else:
            power_log_exists = bool(power_log_path) and Path(power_log_path).is_file()
            checks.append(
                _check(
                    "power_log",
                    power_log_exists,
                    "power_log_missing",
                    f"Power.log path does not exist or is not a file: {power_log_path!r}",
                    success_detail=power_log_path,
                )
            )

    try:
        process_names = _process_names(probes.processes())
    except Exception as exc:
        process_names = set()
        checks.append(
            _check("process_probe", False, "process_probe_error", _safe_detail(exc))
        )
    else:
        checks.append(_check("process_probe", True, success_detail="readable"))
    checks.append(
        _check(
            "hearthstone_process",
            _PROCESS_NAMES["hearthstone"] in process_names,
            "hearthstone_not_running",
            "Hearthstone.exe is not running",
            success_detail="hearthstone.exe",
        )
    )
    checks.append(
        _check(
            "hsang_process",
            _PROCESS_NAMES["hsang"] in process_names,
            "hsang_not_running",
            "HSAng.exe is not running",
            success_detail="hsang.exe",
        )
    )

    phase = "unknown"
    try:
        ui = probes.ui()
        if not isinstance(ui, Mapping):
            raise TypeError("ui probe must return a mapping")
        foreground = ui.get("foreground")
        phase_value = ui.get("phase")
        if type(foreground) is not bool:
            raise TypeError("ui foreground must be an exact bool")
        if not isinstance(phase_value, str) or not phase_value.strip():
            raise TypeError("ui phase must be non-empty text")
        phase = phase_value.strip().lower()
        confidence = ui.get("confidence")
        evidence_id = ui.get("evidence_id", "")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise TypeError("ui confidence must be numeric")
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("ui confidence must be finite and between 0 and 1")
        if not isinstance(evidence_id, str):
            raise TypeError("ui evidence_id must be text")
    except Exception as exc:
        checks.append(_check("foreground", False, "ui_probe_error", _safe_detail(exc)))
        checks.append(_check("ui_state", False, "ui_probe_error", _safe_detail(exc)))
    else:
        evidence_detail = phase
        if confidence is not None:
            evidence_detail += f"; confidence={confidence:.6f}"
        if evidence_id:
            evidence_detail += f"; evidence={evidence_id}"
        checks.append(
            _check(
                "foreground",
                foreground,
                "hearthstone_not_foreground",
                "Hearthstone/HSAng must be foreground and input-ready",
                success_detail=evidence_detail,
            )
        )
        checks.append(
            _check(
                "ui_state",
                phase in SAFE_START_PHASES,
                "unsafe_ui_state",
                f"recognized phase {phase!r}; expected main_menu or deck_select",
                success_detail=evidence_detail,
            )
        )

    try:
        admin = probes.is_admin()
        if type(admin) is not bool:
            raise TypeError("admin probe must return an exact bool")
    except Exception as exc:
        checks.append(_check("admin", False, "admin_probe_error", _safe_detail(exc)))
    else:
        checks.append(
            _check(
                "admin",
                admin,
                "admin_required",
                "Hearthstone input injection requires an elevated process",
                success_detail="elevated",
            )
        )

    try:
        point = probes.deck_slot(deck_index)
        if (
            not isinstance(point, (tuple, list))
            or len(point) != 2
            or any(type(value) is not int for value in point)
        ):
            raise TypeError("deck slot probe must return two integers")
    except Exception as exc:
        checks.append(_check("deck_index", False, "deck_index_invalid", _safe_detail(exc)))
    else:
        checks.append(
            _check("deck_index", True, success_detail=f"x={point[0]}, y={point[1]}")
        )
    return _report(deck_index, checks, phase, live_command=command)


def _default_ui_probe(runtime: Any) -> Mapping[str, Any]:
    hwnd = runtime.get_screen.get_HS_hwnd()
    if not hwnd:
        return {"foreground": False, "phase": "unknown"}
    foreground = runtime.get_screen.is_hearthstone_input_ready(hwnd)
    if type(foreground) is not bool:
        raise TypeError("foreground probe must return an exact bool")
    if not foreground:
        return {"foreground": False, "phase": "unknown"}
    mapping = runtime.display_mapping.display_mapping(hwnd)
    if mapping is None:
        return {"foreground": True, "phase": "unknown"}
    frame = runtime.get_screen._bitblt_region(mapping.client_box)
    if frame is None:
        return {"foreground": True, "phase": "unknown"}
    result = runtime.battle_entry.identify(frame)
    if not isinstance(result, Mapping):
        raise TypeError("UI identify result must be a mapping")
    phase = str(result.get("state") or "unknown")
    confidence = 0.0
    for key in ("traditional", "deck_title", "start"):
        evidence = result.get(key)
        if isinstance(evidence, Mapping):
            try:
                confidence = max(confidence, float(evidence.get("score") or 0.0))
            except (TypeError, ValueError):
                continue
    return {
        "foreground": True,
        "phase": phase,
        "confidence": min(1.0, max(0.0, confidence)),
        "evidence_id": f"battle-entry:{phase}:{confidence:.6f}",
    }


def _default_probes(runtime: Any) -> PreflightProbes:
    def identity() -> tuple[str, str]:
        return (
            str(getattr(runtime.config, "USER_NAME", "") or ""),
            str(getattr(runtime.config, "HEARTHSTONE_LOG_ROOT", "") or ""),
        )

    def power_log(log_root: str) -> str | None:
        result = runtime.power_log.find_latest_power_log(log_root)
        return str(result) if result is not None else None

    def processes() -> tuple[str, ...]:
        return tuple(
            str(process.info.get("name") or "")
            for process in psutil.process_iter(["name"])
        )

    def is_admin() -> bool:
        if not hasattr(ctypes, "windll"):
            return False
        return bool(ctypes.windll.shell32.IsUserAnAdmin())

    return PreflightProbes(
        runtime=lambda: str(runtime.root),
        identity=identity,
        power_log=power_log,
        processes=processes,
        ui=lambda: _default_ui_probe(runtime),
        is_admin=is_admin,
        deck_slot=lambda index: tuple(runtime.battle_entry.deck_slot_point(index + 1)),
    )


def build_native_preflight(
    deck_index: int,
    *,
    live_command: str | None = None,
) -> PreflightReport:
    """Load the native runtime and evaluate readiness without game actions."""

    command = live_command or _default_live_command(deck_index)
    try:
        runtime = _load_legacy_runtime()
    except Exception as exc:
        return PreflightReport(
            ok=False,
            deck_index=deck_index,
            ui_phase="unknown",
            checks=(
                _check(
                    "legacy_runtime",
                    False,
                    "legacy_runtime_unavailable",
                    _safe_detail(exc),
                ),
            ),
            live_command=command,
        )
    return run_preflight(
        deck_index,
        _default_probes(runtime),
        live_command=command,
    )


__all__ = [
    "PreflightCheck",
    "PreflightProbes",
    "PreflightReport",
    "SAFE_START_PHASES",
    "build_native_preflight",
    "run_preflight",
]
