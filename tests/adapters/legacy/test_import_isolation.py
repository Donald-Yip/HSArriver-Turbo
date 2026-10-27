"""Subprocess import-isolation tests for fake/help/native bootstrap paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MARKER = "__R13A_ISOLATION__"
LOADED_CHECK = '''
import json
import sys
from pathlib import Path

FORBIDDEN_EXACT = {
    "battle_entry",
    "hslegend.adapters.legacy.factory",
    "log_op",
    "log_state",
    "src.recommendation_models",
}
FORBIDDEN_PREFIXES = (
    "src.capture",
    "src.ocr",
    "src.parser",
    "src.recommendation_config",
)
root_click = Path(r"ROOT_CLICK").resolve()
loaded = []
for name, module in list(sys.modules.items()):
    if name in FORBIDDEN_EXACT or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        loaded.append(name)
    if name == "click":
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).resolve() == root_click:
            loaded.append("click:legacy-root")
print("MARKER" + json.dumps(sorted(loaded)))
'''


def run_script(body: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    script = body + LOADED_CHECK.replace("MARKER", MARKER).replace(
        "ROOT_CLICK", str(ROOT / "click.py")
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    marker_lines = [
        line.removeprefix(MARKER)
        for line in completed.stdout.splitlines()
        if line.startswith(MARKER)
    ]
    if len(marker_lines) != 1:
        raise AssertionError(
            f"missing isolation marker; stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    return completed, json.loads(marker_lines[0])


class ImportIsolationTests(unittest.TestCase):
    def test_entrypoint_and_bootstrap_imports_do_not_load_legacy_runtime(self) -> None:
        completed, loaded = run_script(
            "import hslegend.bootstrap\n"
            "import hslegend.entrypoints.onegame\n"
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], loaded)

    def test_help_does_not_load_legacy_runtime(self) -> None:
        completed, loaded = run_script(
            "import runpy, sys\n"
            "sys.argv = ['hslegend-onegame', '--help']\n"
            "exit_code = 0\n"
            "try:\n"
            "    runpy.run_module('hslegend.entrypoints.onegame', run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    exit_code = int(exc.code or 0)\n"
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], loaded)

    def test_fake_does_not_load_legacy_runtime(self) -> None:
        completed, loaded = run_script(
            "import runpy, sys\n"
            "sys.argv = ['hslegend-onegame', '--fake', '--json']\n"
            "exit_code = 0\n"
            "try:\n"
            "    runpy.run_module('hslegend.entrypoints.onegame', run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    exit_code = int(exc.code or 0)\n"
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], loaded)
        self.assertIn('"status": "completed"', completed.stdout)

    def test_native_bootstrap_injected_ports_avoid_legacy_runtime(self) -> None:
        completed, loaded = run_script(
            "from hslegend.adapters.demo import DemoScenario\n"
            "from hslegend.adapters.legacy.ports import NativePorts\n"
            "from hslegend.application import ConfigSnapshot\n"
            "from hslegend.bootstrap.native import build_native_one_game\n"
            "class ConfigRepo:\n"
            "    def load(self):\n"
            "        return ConfigSnapshot()\n"
            "class Artifacts:\n"
            "    def store(self, source_ref, *, kind='evidence'):\n"
            "        return source_ref\n"
            "scenario = DemoScenario.build()\n"
            "def ports_builder(token, clock, sleep):\n"
            "    return NativePorts(\n"
            "        config_repository=ConfigRepo(),\n"
            "        card_catalog=scenario.catalog,\n"
            "        recommendation_mulligan=scenario.recommendation,\n"
            "        recommendation_turn=scenario.recommendation,\n"
            "        game_state=scenario.state,\n"
            "        lobby_state=scenario.lobby,\n"
            "        action_dispatch=scenario.dispatch,\n"
            "        journal=scenario.journal,\n"
            "        artifact_store=Artifacts(),\n"
            "    )\n"
            "build_native_one_game(\n"
            "    ports_builder=ports_builder,\n"
            "    clock=lambda: 0.0,\n"
            "    sleep=lambda _seconds: None,\n"
            ")\n"
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], loaded)


if __name__ == "__main__":
    unittest.main()
