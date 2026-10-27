"""Constrained filesystem artifact storage adapter."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath


class ArtifactStoreError(ValueError):
    """Raised when an artifact path escapes its configured root."""


def _relative_parts(reference: str) -> tuple[str, ...]:
    if not isinstance(reference, str) or not reference.strip() or "\0" in reference:
        raise ArtifactStoreError("artifact reference must be a non-empty relative path")
    normalized = reference.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or Path(reference).is_absolute():
        raise ArtifactStoreError("artifact reference must be relative")
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactStoreError("artifact reference may not traverse outside its root")
    if any(":" in part for part in parts):
        raise ArtifactStoreError("artifact reference may not contain a drive or URI")
    return parts


class FileArtifactStore:
    """Store evidence bytes beneath one resolved root directory."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        source_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve(strict=False)
        self._source_root = (
            Path(source_root).expanduser().resolve(strict=False)
            if source_root is not None
            else None
        )

    def resolve(self, reference: str) -> Path:
        parts = _relative_parts(reference)
        target = (self._root / Path(*parts)).resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactStoreError("artifact reference escapes the storage root") from exc
        return target

    def save(self, reference: str, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("artifact data must be bytes-like")
        target = self.resolve(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(bytes(data))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return "/".join(_relative_parts(reference))

    def read_bytes(self, reference: str) -> bytes:
        return self.resolve(reference).read_bytes()

    def store(self, source_ref: str, *, kind: str = "evidence") -> str:
        kind_parts = _relative_parts(kind)
        if self._source_root is None:
            source = Path(source_ref).expanduser().resolve(strict=False)
        else:
            source_parts = _relative_parts(source_ref)
            source = (self._source_root / Path(*source_parts)).resolve(strict=False)
            try:
                source.relative_to(self._source_root)
            except ValueError as exc:
                raise ArtifactStoreError("source reference escapes the source root") from exc
        if not source.is_file():
            raise ArtifactStoreError(f"artifact source is not a file: {source}")
        destination = "/".join((*kind_parts, source.name))
        self.save(destination, source.read_bytes())
        return destination


__all__ = ["ArtifactStoreError", "FileArtifactStore"]
