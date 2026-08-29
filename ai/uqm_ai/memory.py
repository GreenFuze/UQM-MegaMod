"""What a character remembers of earlier meetings.

Summarised, never a transcript: one short line per encounter, so prompt growth
is bounded by how many times the player has visited rather than by how long
they talked.

SAVE SAFETY IS THE WHOLE DESIGN PROBLEM HERE, and it is why this is smaller
than docs/ai-architecture.md section 5 describes.

The obvious implementation - a file per save under ai/data - is not safe. The
game has no global naming the current save slot, so every playthrough would
share one store; and loading a save from before a revelation would leave the
side-file still holding it, so the character would remember a conversation that
has not happened in that timeline and spoil the plot to prove it. There is no
fix short of writing into the save format itself.

So memory lives in the sidecar process and dies with it, and it is guarded by
the one monotonic thing the game does send: the in-game date.

    If the incoming date is EARLIER than the newest thing remembered, the
    player has loaded an earlier save. Everything after the new date is
    dropped.

That makes recall correct within a session, including across a load, and
correct by construction across sessions because there is nothing to carry over.
What is lost is a character remembering you between launches. That is worth
having and is not worth being wrong about; it needs a real save identity first.

Two rules from the architecture still hold absolutely:

  1. Memory colours dialogue and nothing else. The only state-changing path is
     `action`, which is validated against the actions exported this turn, so
     nothing written here can reach game state however the model phrases it.
  2. It is per character. The Ur-Quan do not know what Fwiffo told you.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Enough for the prompt to feel continuous, few enough that it cannot grow
# without bound over a long game. Oldest are dropped first.
MAX_ENTRIES_PER_CHARACTER = 8


@dataclass(frozen=True)
class Recollection:
    """One remembered encounter."""

    when: date
    text: str


class MemoryStore:
    """Per-character recollections for the current play session."""

    def __init__(self, limit: int = MAX_ENTRIES_PER_CHARACTER) -> None:
        self._limit = limit
        self._by_character: dict[str, list[Recollection]] = {}

    def remember(self, character: str, when: date, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            return

        entries = self._by_character.setdefault(character, [])
        if any(e.text == text for e in entries):
            return          # the same beat twice is not a second memory

        entries.append(Recollection(when, text))
        entries.sort(key=lambda e: e.when)
        del entries[: max(0, len(entries) - self._limit)]

    def recall(self, character: str, now: date) -> tuple[str, ...]:
        """What this character may recall, given where the story now is.

        Anything dated after `now` is discarded rather than hidden: the player
        has loaded an earlier save, and that future did not happen.
        """
        entries = self._by_character.get(character)
        if not entries:
            return ()

        kept = [e for e in entries if e.when <= now]
        if len(kept) != len(entries):
            self._by_character[character] = kept
        return tuple(e.text for e in kept)

    def forget(self, character: str) -> None:
        self._by_character.pop(character, None)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_character.values())
