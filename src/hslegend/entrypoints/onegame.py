"""Stdlib-only command line for exactly one Hearthstone game."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

from hslegend.application import OneGameCommand, OneGameStatus
from hslegend.bootstrap import build_fake_one_game


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not parsed > 0.0 or parsed == float("inf"):
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def _non_empty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must be non-empty text")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hslegend-onegame",
        description="Run exactly one Hearthstone game.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fake",
        action="store_true",
        help="use deterministic demo adapters instead of the native bridge",
    )
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="run read-only native readiness checks without playing",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="explicitly authorize native Hearthstone input",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write exactly one machine-readable object to stdout",
    )
    parser.add_argument("--command-id", type=_non_empty_text, default="cli-onegame")
    parser.add_argument("--deck-index", type=_non_negative_int, default=0)
    parser.add_argument("--max-turns", type=_positive_int, default=20)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=900.0)
    parser.add_argument(
        "--enter-battle-timeout-seconds",
        type=_positive_float,
        default=60.0,
    )
    parser.add_argument(
        "--mulligan-timeout-seconds",
        type=_positive_float,
        default=20.0,
    )
    parser.add_argument(
        "--turn-timeout-seconds",
        type=_positive_float,
        default=90.0,
    )
    parser.add_argument(
        "--max-actions-per-turn",
        type=_positive_int,
        default=10,
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=0.25,
    )
    return parser


def _use_case_from_builder(builder: Callable[[], Any]) -> Any:
    built = builder()
    return getattr(built, "use_case", built)


def _reason_code(exc: Exception) -> str:
    value = getattr(exc, "reason_code", "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "cli_error"


def _live_command(args: argparse.Namespace) -> str:
    return subprocess.list2cmdline(
        [
            "uv",
            "run",
            "hslegend-onegame",
            "--allow-live",
            "--command-id",
            args.command_id,
            "--deck-index",
            str(args.deck_index),
            "--max-turns",
            str(args.max_turns),
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--enter-battle-timeout-seconds",
            str(args.enter_battle_timeout_seconds),
            "--mulligan-timeout-seconds",
            str(args.mulligan_timeout_seconds),
            "--turn-timeout-seconds",
            str(args.turn_timeout_seconds),
            "--max-actions-per-turn",
            str(args.max_actions_per_turn),
            "--poll-interval-seconds",
            str(args.poll_interval_seconds),
            "--json",
        ]
    )


def _validate_preflight_report(report: Any) -> tuple[bool, str]:
    checks = getattr(report, "checks", None)
    if not isinstance(checks, (tuple, list)) or not checks:
        return False, "preflight checks are missing"
    report_ok = getattr(report, "ok", None)
    if type(report_ok) is not bool:
        return False, "preflight ok must be an exact bool"
    if type(getattr(report, "deck_index", None)) is not int:
        return False, "preflight deck_index must be an int"
    if int(report.deck_index) < 0:
        return False, "preflight deck_index must be non-negative"
    phase = getattr(report, "ui_phase", None)
    if not isinstance(phase, str) or not phase.strip():
        return False, "preflight ui_phase must be non-empty text"
    command = getattr(report, "live_command", None)
    if not isinstance(command, str) or not command.strip():
        return False, "preflight live_command must be non-empty text"
    for check in checks:
        if type(getattr(check, "ok", None)) is not bool:
            return False, "preflight check ok must be an exact bool"
        check_id = getattr(check, "check_id", None)
        if not isinstance(check_id, str) or not check_id.strip():
            return False, "preflight check_id must be non-empty text"
        reason = getattr(check, "reason_code", None)
        detail = getattr(check, "detail", None)
        if not isinstance(reason, str) or not isinstance(detail, str):
            return False, "preflight check reason/detail must be text"
        if check.ok is False and not reason.strip():
            return False, "failed preflight check requires reason_code"
    if report_ok != all(check.ok for check in checks):
        return False, "preflight ok contradicts its checks"
    return True, ""


def _preflight_exit(*, json_output: bool, report: Any) -> int:
    valid, invalid_reason = _validate_preflight_report(report)
    if not valid:
        error = {
            "ok": False,
            "reason_code": "preflight_report_invalid",
            "message": invalid_reason,
        }
        if json_output:
            print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        else:
            print("preflight report invalid: " + invalid_reason, file=sys.stderr)
        return 1
    payload = {
        "ok": report.ok,
        "deck_index": report.deck_index,
        "ui_phase": report.ui_phase,
        "checks": [
            {
                "check_id": check.check_id,
                "ok": check.ok,
                "reason_code": check.reason_code,
                "detail": check.detail,
            }
            for check in report.checks
        ],
        "live_command": report.live_command,
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for check in report.checks:
            state = "ok" if check.ok else "fail"
            reason = getattr(check, "reason_code", "")
            detail = getattr(check, "detail", "")
            suffix = " (" + reason + ": " + detail + ")" if reason or detail else ""
            print("[{}] {}{}".format(state, check.check_id, suffix))
    return 0 if report.ok else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    native_builder: Callable[[], Any] | None = None,
    fake_builder: Callable[[], Any] | None = None,
    preflight_builder: Callable[..., Any] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.fake and args.allow_live:
        parser.error("--allow-live is only valid for native execution")
    if args.preflight and args.allow_live:
        parser.error("--preflight is read-only and does not accept --allow-live")

    if args.preflight:
        if preflight_builder is None:
            from hslegend.bootstrap.native import build_native_preflight

            preflight_builder = build_native_preflight
        try:
            report = preflight_builder(
                args.deck_index,
                live_command=_live_command(args),
            )
        except Exception as exc:
            error = {
                "ok": False,
                "deck_index": args.deck_index,
                "reason_code": "preflight_error",
                "message": str(exc) or type(exc).__name__,
            }
            if args.json:
                print(json.dumps(error, ensure_ascii=False, sort_keys=True))
            else:
                print("preflight failed: " + error["message"], file=sys.stderr)
            return 1
        return _preflight_exit(
            json_output=args.json,
            report=report,
        )

    if not args.fake and not args.allow_live:
        parser.error("--allow-live is required for native Hearthstone input")

    try:
        command = OneGameCommand(
            command_id=args.command_id,
            deck_index=args.deck_index,
            max_turns=args.max_turns,
            timeout_seconds=args.timeout_seconds,
            enter_battle_timeout_seconds=args.enter_battle_timeout_seconds,
            mulligan_timeout_seconds=args.mulligan_timeout_seconds,
            turn_timeout_seconds=args.turn_timeout_seconds,
            max_actions_per_turn=args.max_actions_per_turn,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    if args.fake:
        builder = fake_builder or build_fake_one_game
    else:
        if native_builder is None:
            from hslegend.bootstrap.native import build_native_one_game

            native_builder = build_native_one_game
        builder = native_builder
    try:
        use_case = _use_case_from_builder(builder)
        result = use_case.execute(command)
    except Exception as exc:
        error = {
            "command_id": command.command_id,
            "session_id": command.command_id,
            "status": OneGameStatus.FAILED.value,
            "reason_code": _reason_code(exc),
            "message": str(exc) or type(exc).__name__,
        }
        if args.json:
            print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        else:
            print(f"failed: {error['message']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_primitive(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"{result.status.value}: {result.reason_code} "
            f"(turns={result.turns_played}, actions={result.actions_played})"
        )
    return 0 if result.status is OneGameStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
