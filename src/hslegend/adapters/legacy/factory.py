"""Native port factory for the isolated legacy anti-corruption package."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from hslegend.adapters.artifacts import FileArtifactStore
from hslegend.adapters.cards import DEFAULT_RULES_PATH, JsonCardCatalog
from hslegend.adapters.config import JsonConfigRepository
from hslegend.adapters.journal import JsonlRunJournal

from .hsang import StableHsangRecommendationSource
from .input import LegacyClickActionDispatcher
from .paths import legacy_paths
from .ports import NativePorts


class LegacyRuntimeUnavailable(RuntimeError):
    """Typed failure when the source-tree legacy runtime cannot be loaded."""

    reason_code = "legacy_runtime_unavailable"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        message = self.reason_code if not detail else f"{self.reason_code}: {detail}"
        super().__init__(message)


_REQUIRED_LEGACY_PATHS = (
    "battle_entry.py",
    "cards.json",
    "click.py",
    "config.py",
    "display_mapping.py",
    "get_screen.py",
    "log_op.py",
    "log_state.py",
    "power_log.py",
    "src/capture/desktop_capture.py",
    "src/ocr/paddle_adapter.py",
    "src/ocr/stable_reader.py",
    "src/parser/recommendation_parser.py",
    "src/recommendation_config.py",
)


def _legacy_root_candidates(explicit: str | Path | None) -> tuple[Path, ...]:
    override = explicit if explicit is not None else os.environ.get(
        "HSLEGEND_LEGACY_ROOT"
    )
    if override:
        return (Path(override).expanduser().resolve(),)
    return (
        Path(__file__).resolve().parents[4],
        Path.cwd().resolve(),
    )


def resolve_legacy_root(explicit: str | Path | None = None) -> Path:
    """Resolve an explicit/env override or the source-tree legacy root."""

    candidates = _legacy_root_candidates(explicit)
    missing: list[str] = []
    for candidate in candidates:
        absent = [
            relative
            for relative in _REQUIRED_LEGACY_PATHS
            if not (candidate / relative).is_file()
        ]
        if not absent:
            return candidate
        missing.append(f"{candidate}: missing {', '.join(absent[:3])}")
    detail = "no complete legacy runtime found"
    if missing:
        detail += " (" + "; ".join(missing) + ")"
    raise LegacyRuntimeUnavailable(detail)


def _load_click_module(root: Path) -> ModuleType:
    name = "_hslegend_legacy_click"
    spec = importlib.util.spec_from_file_location(name, root / "click.py")
    if spec is None or spec.loader is None:
        raise LegacyRuntimeUnavailable("cannot load legacy click.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise LegacyRuntimeUnavailable(f"cannot load legacy click.py: {exc}") from exc
    return module


def _load_legacy_runtime(root_path: str | Path | None = None) -> SimpleNamespace:
    """Import stable legacy modules only when the native builder is called."""

    root = resolve_legacy_root(root_path)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    legacy_click = _load_click_module(root)
    previous_click = sys.modules.get("click")
    sys.modules["click"] = legacy_click
    try:
        battle_entry = importlib.import_module("battle_entry")
        config = importlib.import_module("config")
        display_mapping = importlib.import_module("display_mapping")
        get_screen = importlib.import_module("get_screen")
        log_op = importlib.import_module("log_op")
        power_log = importlib.import_module("power_log")
        desktop_capture = importlib.import_module("src.capture.desktop_capture")
        paddle_adapter = importlib.import_module("src.ocr.paddle_adapter")
        stable_reader = importlib.import_module("src.ocr.stable_reader")
        parser_module = importlib.import_module("src.parser.recommendation_parser")
        recommendation_config = importlib.import_module("src.recommendation_config")
    except Exception as exc:
        raise LegacyRuntimeUnavailable(
            f"cannot import legacy runtime from {root}: {exc}"
        ) from exc
    finally:
        if previous_click is None:
            sys.modules.pop("click", None)
        else:
            sys.modules["click"] = previous_click

    return SimpleNamespace(
        root=root,
        battle_entry=battle_entry,
        click=legacy_click,
        config=config,
        display_mapping=display_mapping,
        get_screen=get_screen,
        log_op=log_op,
        power_log=power_log,
        DesktopCapture=desktop_capture.DesktopCapture,
        PaddleOcrAdapter=paddle_adapter.PaddleOcrAdapter,
        StableRecommendationReader=stable_reader.StableRecommendationReader,
        RecommendationParser=parser_module.RecommendationParser,
        RecommendationConfig=recommendation_config.RecommendationConfig,
    )


def _ui_recognizer(runtime: SimpleNamespace, timeout_seconds: float | None = None) -> Any:
    del timeout_seconds
    frame, _mapping = runtime.battle_entry.capture_client_frame()
    if frame is None:
        return {"state": "unknown", "evidence_id": "battle-entry:capture-missing"}
    result = runtime.battle_entry.identify(frame)
    state = str(result.get("state") or "unknown")
    score = 0.0
    for key in ("traditional", "deck_title", "start"):
        evidence = result.get(key)
        if isinstance(evidence, dict):
            try:
                score = max(score, float(evidence.get("score") or 0.0))
            except (TypeError, ValueError):
                pass
    confidence = 1.0 if state != "unknown" else 0.0
    return {
        "state": state,
        "confidence": confidence,
        "observed_at": None,
        "evidence_id": f"battle-entry:{state}:{score:.6f}",
    }


def _mulligan_stability_key(parser: Any) -> Callable[[Any, str], Any]:
    def stability_key(evidence: Any, text: str) -> Any:
        normalized = parser.normalize_action_text(text)
        try:
            proposed = parser.parse(evidence, 0, 0)
        except Exception:
            return ("text", normalized)
        action = getattr(proposed, "action", None)
        action_value = str(getattr(action, "value", action) or "").strip().lower()
        if action_value == "mulligan":
            return ("mulligan", tuple(getattr(proposed, "mulligan_slots", ()) or ()))
        return ("text", normalized)

    return stability_key


def _stable_recommendation_source(
    *,
    config: Any,
    backend: Any,
    capture: Any,
    parser: Any,
    sleep: Callable[[float], None],
    mulligan: bool,
    reader_factory: Callable[..., Any],
) -> StableHsangRecommendationSource:
    reader = reader_factory(
        config,
        backend,
        sleep=sleep,
        required_headers=(),
        text_normalizer=parser.normalize_action_text,
        stability_key=_mulligan_stability_key(parser) if mulligan else None,
    )
    return StableHsangRecommendationSource(
        lambda: capture.capture(ocr_panel_ok=mulligan),
        reader,
        parser.parse,
        mulligan_roi_supplier=capture.crop_recommendation,
        turn_roi_supplier=capture.crop_recommendation,
    )


def _build_stable_recommendation_sources(
    *,
    config: Any,
    backend: Any,
    parser: Any,
    mulligan_capture: Any,
    turn_capture: Any,
    sleep: Callable[[float], None],
    reader_factory: Callable[..., Any],
) -> tuple[StableHsangRecommendationSource, StableHsangRecommendationSource]:
    mulligan = _stable_recommendation_source(
        config=config,
        backend=backend,
        capture=mulligan_capture,
        parser=parser,
        sleep=sleep,
        mulligan=True,
        reader_factory=reader_factory,
    )
    turn = _stable_recommendation_source(
        config=config,
        backend=backend,
        capture=turn_capture,
        parser=parser,
        sleep=sleep,
        mulligan=False,
        reader_factory=reader_factory,
    )
    return mulligan, turn


def build_native_ports(
    stop_token: Any,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    *,
    legacy_root: str | Path | None = None,
    runtime: SimpleNamespace | None = None,
) -> NativePorts:
    """Assemble native adapters without importing or touching them before call."""

    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    if not callable(clock):
        raise TypeError("clock must be callable")
    if not callable(sleep):
        raise TypeError("sleep must be callable")
    if not callable(getattr(stop_token, "is_cancelled", None)):
        raise TypeError("stop_token must provide is_cancelled()")

    runtime = runtime or _load_legacy_runtime(legacy_root)
    from .lobby import AuthoritativeLobbyStateAdapter
    from .powerlog import LegacyLogStateSnapshotMapper, LegacyPowerLogPump
    from hslegend.adapters.lobby import LegacyLobbyStateAdapter
    from hslegend.adapters.powerlog import PowerLogGameStateAdapter

    paths = legacy_paths(runtime.config)
    config_repository = JsonConfigRepository(paths.config_path)
    rules_path = paths.root / "rules" / DEFAULT_RULES_PATH.name
    card_catalog = JsonCardCatalog(paths.cards_path, rules_path=rules_path)
    journal = JsonlRunJournal(paths.journal_path)
    artifact_store = FileArtifactStore(paths.artifact_root)

    log_root = paths.log_root if paths.log_root is not None else ""
    containers = runtime.log_op.log_iter_func(str(log_root))
    pump = LegacyPowerLogPump(containers, clock=clock)
    mapper = LegacyLogStateSnapshotMapper(pump, clock=clock)
    game_state = PowerLogGameStateAdapter(
        mapper.current,
        mapper.revision,
        clock=clock,
        sleep_func=sleep,
        stop_token=stop_token,
    )
    ui_lobby = LegacyLobbyStateAdapter(
        lambda: _ui_recognizer(runtime),
        clock=clock,
    )
    lobby_state = AuthoritativeLobbyStateAdapter(pump, ui_lobby, clock=clock)

    recommendation_config = runtime.RecommendationConfig()
    parser = runtime.RecommendationParser()
    backend = runtime.PaddleOcrAdapter(clock=clock)
    mulligan_capture = runtime.DesktopCapture(
        recommendation_config,
        roi=recommendation_config.mulligan_recommendation_roi,
        clock=clock,
    )
    turn_capture = runtime.DesktopCapture(
        recommendation_config,
        roi=recommendation_config.recommendation_roi,
        clock=clock,
    )
    recommendation_mulligan, recommendation_turn = _build_stable_recommendation_sources(
        config=recommendation_config,
        backend=backend,
        parser=parser,
        mulligan_capture=mulligan_capture,
        turn_capture=turn_capture,
        sleep=sleep,
        reader_factory=runtime.StableRecommendationReader,
    )

    def card_type(card_id: str) -> str | None:
        definition = card_catalog.get_card(card_id)
        return definition.card_type if definition is not None else None

    action_dispatch = LegacyClickActionDispatcher(
        runtime.click,
        battle_entry=runtime.battle_entry,
        card_type_resolver=card_type,
    )
    return NativePorts(
        config_repository=config_repository,
        card_catalog=card_catalog,
        recommendation_mulligan=recommendation_mulligan,
        recommendation_turn=recommendation_turn,
        game_state=game_state,
        lobby_state=lobby_state,
        action_dispatch=action_dispatch,
        journal=journal,
        artifact_store=artifact_store,
    )


__all__ = [
    "LegacyRuntimeUnavailable",
    "_build_stable_recommendation_sources",
    "_mulligan_stability_key",
    "build_native_ports",
    "resolve_legacy_root",
]
