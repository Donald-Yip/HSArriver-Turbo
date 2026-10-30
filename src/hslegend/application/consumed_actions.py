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

    def discard(self, key: tuple[object, ...]) -> None:
        """Forget a consumption mark so a provably-lost action can retry.

        Only used when a pending confirmation timed out with the same
        recommendation still on screen: the click was dropped (for example the
        client was still animating), so replaying it is safe and required to
        avoid stalling the whole turn.
        """
        self._keys.discard(key)


__all__ = ["ConsumedActionStore"]
