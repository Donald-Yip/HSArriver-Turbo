"""Filesystem-backed configuration repository."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from hslegend.application import ConfigSnapshot


class ConfigCorruptionError(ValueError):
    """Raised when an existing config cannot be parsed safely."""


@lru_cache(maxsize=None)
def _path_thread_lock(path_key: str) -> threading.RLock:
    return threading.RLock()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Serialize access through an in-process lock and an OS file lock."""

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


def _deep_merge(current: Mapping[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {str(key): value for key, value in current.items()}
    for key, value in updates.items():
        name = str(key)
        previous = merged.get(name)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            merged[name] = _deep_merge(previous, value)
        else:
            merged[name] = value
    return merged


def _snapshot_from_mapping(raw: Mapping[str, Any]) -> ConfigSnapshot:
    schema_version = raw.get("schema_version", 1)
    if type(schema_version) is not int or schema_version < 0:
        raise ConfigCorruptionError("schema_version must be a non-negative integer")
    values = tuple(
        (str(key), raw[key])
        for key in sorted(raw, key=lambda item: str(item))
        if str(key) != "schema_version"
    )
    return ConfigSnapshot(values=values, schema_version=schema_version)


def _atomic_replace(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class JsonConfigRepository:
    """Load and atomically merge a JSON configuration file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path).expanduser()
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")

    def load(self) -> ConfigSnapshot:
        with _file_lock(self._lock_path):
            raw = self._read_locked()
            try:
                return _snapshot_from_mapping(raw)
            except (TypeError, ValueError) as exc:
                self._quarantine(exc)

    def save(self, updates: Mapping[str, Any]) -> ConfigSnapshot:
        if not isinstance(updates, Mapping):
            raise TypeError("config updates must be a mapping")
        with _file_lock(self._lock_path):
            current = self._read_locked()
            merged = _deep_merge(current, updates)
            text = json.dumps(
                merged,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            ) + "\n"
            persisted = json.loads(text)
            snapshot = _snapshot_from_mapping(persisted)
            _atomic_replace(self._path, text)
            return snapshot

    def _read_locked(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            parsed = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._quarantine(exc)
        if not isinstance(parsed, dict):
            self._quarantine(ConfigCorruptionError("config root must be a JSON object"))
        return parsed

    def _quarantine(self, cause: Exception) -> NoReturn:
        backup = self._path.with_name(f"{self._path.name}.corrupt-{uuid.uuid4().hex}")
        os.replace(self._path, backup)
        raise ConfigCorruptionError(f"corrupt config moved to {backup}") from cause


__all__ = ["ConfigCorruptionError", "JsonConfigRepository"]
