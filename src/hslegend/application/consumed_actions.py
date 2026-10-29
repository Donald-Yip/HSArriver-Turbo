"""Application-level consumed-action identity store."""

from __future__ import annotations


class ConsumedActionStore:
    """Remember which executed recommendation identities were consumed."""

    def __init__(self) -> None:
        self._keys: set[tuple[object, ...]] = set()

    @staticmethod
    def key_for(proposed, adapted) -> tuple[object, ...]:
        return (
            proposed.turn_number,
            proposed.normalized_instruction,
            adapted.source_entity_id,
            adapted.target_entity_id,
            getattr(adapted, "postcondition", ""),
        )

    def contains(self, key: tuple[object, ...]) -> bool:
        return key in self._keys

    def mark_consumed(self, key: tuple[object, ...]) -> None:
        self._keys.add(key)


__all__ = ["ConsumedActionStore"]
