"""Stdlib-only command line for exactly one Hearthstone game."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

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
        description="Run exactly one headless Hearthstone game.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="use deterministic demo adapters (required until R13 native cutover)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write exactly one machine-readable result object to stdout",
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.fake:
        parser.error("--fake is required; native adapters arrive in R13")

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
    try:
        result = build_fake_one_game().use_case.execute(command)
    except Exception as exc:
        error = {
            "command_id": command.command_id,
            "session_id": command.command_id,
            "status": OneGameStatus.FAILED.value,
            "reason_code": "cli_error",
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
