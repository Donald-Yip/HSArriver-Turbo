"""Durable JSONL run journal adapter."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from hslegend.application import ExecutionRecord, JournalAppendResult


class JournalError(ValueError):
    """Raised when journal data cannot be decoded safely."""


@lru_cache(maxsize=None)
def _path_thread_lock(path_key: str) -> threading.RLock:
    return threading.RLock()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _path_thread_lock(key):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
        acquired = False
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + 10.0
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"timed out acquiring {path}") from exc
                        time.sleep(0.01)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                acquired = True
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


class JsonlRunJournal:
    """Append immutable execution records as one JSON object per line."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path).expanduser()
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")

    def append(self, record: ExecutionRecord) -> JournalAppendResult:
        if not isinstance(record, ExecutionRecord):
            raise TypeError("record must be an ExecutionRecord")
        data = (
            json.dumps(
                record.to_primitive(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        with _file_lock(self._lock_path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "ab", buffering=0) as handle:
                handle.write(data)
                os.fsync(handle.fileno())
        return JournalAppendResult(
            record_id=record.record_id,
            durable=True,
            duplicate=False,
            evidence_ids=record.evidence_ids,
        )

    def read_records(self) -> tuple[ExecutionRecord, ...]:
        with _file_lock(self._lock_path):
            if not self._path.exists():
                return ()
            records: list[ExecutionRecord] = []
            with open(self._path, "r", encoding="utf-8", newline="") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload: Any = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise JournalError(f"invalid journal JSON on line {line_number}") from exc
                    if not isinstance(payload, dict):
                        raise JournalError(f"journal line {line_number} must be an object")
                    try:
                        records.append(ExecutionRecord(**payload))
                    except (TypeError, ValueError) as exc:
                        raise JournalError(f"invalid execution record on line {line_number}") from exc
            return tuple(records)


__all__ = ["JournalError", "JsonlRunJournal"]
