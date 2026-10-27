"""Lazy runtime and native HSAng construction tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hslegend.adapters.legacy.factory import (
    LegacyRuntimeUnavailable,
    _REQUIRED_LEGACY_PATHS,
    _build_stable_recommendation_sources,
    _mulligan_stability_key,
    resolve_legacy_root,
)


class _FakeParser:
    def normalize_action_text(self, text: str) -> str:
        return f"normalized:{text}"

    def parse(self, evidence, turn_number: int, log_revision: int):
        del evidence, turn_number, log_revision
        return SimpleNamespace(
            action=SimpleNamespace(value="mulligan"),
            mulligan_slots=(2, 4),
        )


class _FakeCapture:
    def capture(self, *, ocr_panel_ok: bool):
        return f"frame:{ocr_panel_ok}"

    def crop_recommendation(self, frame):
        return frame


class _FakeReader:
    instances: list["_FakeReader"] = []

    def __init__(self, config, backend, **kwargs) -> None:
        self.config = config
        self.backend = backend
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def read(self, frame_supplier, roi_supplier):
        frame = frame_supplier()
        return roi_supplier(frame)


class NativeFactoryConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeReader.instances.clear()

    def test_native_reader_construction_matches_legacy_hsang_behavior(self) -> None:
        parser = _FakeParser()
        config = object()
        backend = object()

        mulligan, turn = _build_stable_recommendation_sources(
            config=config,
            backend=backend,
            parser=parser,
            mulligan_capture=_FakeCapture(),
            turn_capture=_FakeCapture(),
            sleep=lambda _seconds: None,
            reader_factory=_FakeReader,
        )

        self.assertIsNotNone(mulligan)
        self.assertIsNotNone(turn)
        self.assertEqual(2, len(_FakeReader.instances))
        mulligan_reader, turn_reader = _FakeReader.instances

        for reader in (mulligan_reader, turn_reader):
            self.assertIs(backend, reader.backend)
            self.assertEqual((), reader.kwargs["required_headers"])
            normalizer = reader.kwargs["text_normalizer"]
            self.assertIs(normalizer.__self__, parser)
            self.assertIs(normalizer.__func__, parser.normalize_action_text.__func__)

        self.assertIsNone(turn_reader.kwargs["stability_key"])
        stability_key = mulligan_reader.kwargs["stability_key"]
        self.assertIsNotNone(stability_key)
        assert stability_key is not None
        self.assertEqual(
            ("mulligan", (2, 4)),
            stability_key("evidence", "替换2号位卡牌"),
        )

        class _FailingParser(_FakeParser):
            def parse(self, evidence, turn_number: int, log_revision: int):
                raise ValueError("parse failed")

        fallback = _mulligan_stability_key(_FailingParser())
        self.assertEqual(
            ("text", "normalized:替换1号位卡牌"),
            fallback("evidence", "替换1号位卡牌"),
        )

    def test_runtime_resolution_supports_env_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hslegend-legacy-root-") as temp:
            root = Path(temp)
            for relative in _REQUIRED_LEGACY_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"HSLEGEND_LEGACY_ROOT": str(root)},
                clear=False,
            ):
                self.assertEqual(root.resolve(), resolve_legacy_root())

    def test_missing_runtime_is_typed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hslegend-missing-root-") as temp:
            with self.assertRaises(LegacyRuntimeUnavailable) as raised:
                resolve_legacy_root(temp)
        self.assertEqual("legacy_runtime_unavailable", raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
