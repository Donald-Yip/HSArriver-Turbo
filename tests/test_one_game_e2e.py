from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hslegend.application import LobbyPhase, OneGameCommand, OneGameStatus
from hslegend.application.use_cases.enter_battle import LobbyAction
from hslegend.application.use_cases.finish_battle import FinishBattleStatus
from hslegend.application.use_cases.mulligan import MulliganStatus
from hslegend.application.use_cases.turn import PlayTurnStatus
from hslegend.bootstrap import build_fake_one_game
from hslegend.entrypoints.onegame import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ("-m", "hslegend.entrypoints.onegame")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, *ENTRYPOINT, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class FakeOneGameEndToEndTests(unittest.TestCase):
    def test_real_r08_r11_path_stops_after_one_settlement(self) -> None:
        harness = build_fake_one_game()
        result = harness.use_case.execute(
            OneGameCommand(
                "e2e-one-game",
                deck_index=0,
                max_turns=20,
                timeout_seconds=900.0,
            )
        )

        self.assertIs(OneGameStatus.COMPLETED, result.status)
        self.assertEqual("completed", result.reason_code)
        self.assertEqual("e2e-one-game", result.command_id)
        self.assertEqual("e2e-one-game", result.session_id)
        self.assertEqual(1, result.turns_played)
        self.assertEqual(1, result.actions_played)
        self.assertIs(LobbyPhase.RESULT, result.last_lobby_phase)
        self.assertEqual(len(result.evidence_ids), len(set(result.evidence_ids)))

        self.assertEqual(
            (
                "enter_traditional",
                "select_deck",
                "start_match",
                "mulligan_confirm",
                "end_turn",
            ),
            harness.scenario.dispatch.action_types,
        )
        self.assertEqual(5, len(harness.scenario.dispatch.calls))
        self.assertIs(LobbyPhase.RESULT, harness.scenario.lobby.phase)

        self.assertIsNotNone(result.enter_battle)
        assert result.enter_battle is not None
        self.assertEqual(
            (
                LobbyAction.ENTER_TRADITIONAL,
                LobbyAction.SELECT_DECK,
                LobbyAction.START_MATCH,
            ),
            tuple(dispatch.action for dispatch in result.enter_battle.dispatches),
        )
        self.assertIsNotNone(result.mulligan)
        assert result.mulligan is not None
        self.assertIs(MulliganStatus.COMPLETED, result.mulligan.status)
        self.assertEqual((), result.mulligan.selected_indexes)
        self.assertIsNotNone(result.mulligan.execution_record)

        self.assertEqual(1, len(result.turn_results))
        turn = result.turn_results[0]
        self.assertIs(PlayTurnStatus.ENDED, turn.status)
        self.assertEqual("end_turn", turn.reason_code)
        self.assertEqual(1, turn.actions)
        self.assertEqual(1, len(turn.action_records))

        self.assertIsNotNone(result.finish_battle)
        assert result.finish_battle is not None
        self.assertIs(FinishBattleStatus.COMPLETED, result.finish_battle.status)
        self.assertIs(LobbyPhase.RESULT, result.finish_battle.phase)

        required_evidence = {
            "demo-mulligan-snapshot",
            "recommendation-mulligan",
            "dispatch-mulligan_confirm-4",
            "demo-turn-snapshot",
            "recommendation-turn",
            "dispatch-end_turn-5",
            "demo-result-snapshot",
            "lobby-result",
        }
        self.assertTrue(required_evidence.issubset(set(result.evidence_ids)))
        self.assertEqual(2, len(harness.scenario.journal.records))


class OneGameCliTests(unittest.TestCase):
    def test_help_is_available(self) -> None:
        completed = run_cli("--help")
        self.assertEqual(0, completed.returncode)
        self.assertIn("hslegend-onegame", completed.stdout)
        self.assertIn("--fake", completed.stdout)
        self.assertEqual("", completed.stderr)

    def test_default_selects_native_builder(self) -> None:
        calls: list[str] = []

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        def fake_builder():
            calls.append("fake")
            return build_fake_one_game()

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--json"],
                native_builder=native_builder,
                fake_builder=fake_builder,
            )

        self.assertEqual(0, code)
        self.assertEqual(["native"], calls)
        self.assertEqual("completed", json.loads(output.getvalue())["status"])

    def test_fake_selects_fake_builder(self) -> None:
        calls: list[str] = []

        def native_builder():
            calls.append("native")
            return build_fake_one_game()

        def fake_builder():
            calls.append("fake")
            return build_fake_one_game()

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                ["--fake", "--json"],
                native_builder=native_builder,
                fake_builder=fake_builder,
            )

        self.assertEqual(0, code)
        self.assertEqual(["fake"], calls)
        self.assertEqual("completed", json.loads(output.getvalue())["status"])

    def test_empty_command_id_is_usage_error(self) -> None:
        completed = run_cli("--fake", "--json", "--command-id", "")
        self.assertEqual(2, completed.returncode)
        self.assertIn("must be non-empty text", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_fake_json_is_one_machine_readable_object(self) -> None:
        completed = run_cli("--fake", "--json")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("cli-onegame", payload["command_id"])
        self.assertEqual("completed", payload["status"])
        self.assertEqual("completed", payload["reason_code"])
        self.assertEqual(1, payload["turns_played"])
        self.assertEqual(1, payload["actions_played"])
        self.assertEqual("result", payload["last_lobby_phase"])
        self.assertIsInstance(payload["evidence_ids"], list)
        self.assertIn("recommendation-turn", payload["evidence_ids"])
        self.assertIn("dispatch-end_turn-5", payload["evidence_ids"])
        self.assertIn("lobby-result", payload["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
