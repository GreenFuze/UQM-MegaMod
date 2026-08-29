"""What a character remembers of earlier meetings, and how long ago they were.

One short summary per encounter, written after the conversation ends rather
than during it, so prompt growth is bounded by how many times the player has
visited and never by how long they talked.

SAVE SAFETY is the constraint that shapes this, and it has two halves.

The first is identity. The game has no global naming the current save, so
`SaveGame`/`LoadGame` now record `which_game` and it reaches us as
`session_save_id`. Without that, every playthrough would share one store and a
character would greet a brand-new captain by name.

The second is direction of travel, and it is the subtle one. Loading a save
from before a revelation must not leave a character remembering a conversation
that has not happened in that timeline - it would spoil the plot to prove it.
The guard is the in-game date, which is the one monotonic thing the game sends:

    If the incoming date is EARLIER than the newest thing remembered, the
    player has loaded an earlier save. Everything after the new date is
    dropped, not hidden.

Two rules from the architecture hold absolutely:

  1. Memory colours dialogue and nothing else. The only state-changing path is
     `action`, validated against the actions exported this turn, so nothing
     written here can reach game state however the model phrases it.
  2. It is per character. The Ur-Quan do not know what Fwiffo told you.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Enough for a conversation to feel continuous, few enough that it cannot grow
# without bound over a long game. Oldest are dropped first.
MAX_ENTRIES_PER_CHARACTER = 8


def elapsed(then: date, now: date) -> str:
    """How long ago, in words a character would actually use."""
    days = (now - then).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        weeks = days // 7
        return "a week ago" if weeks == 1 else f"{weeks} weeks ago"
    if days < 730:
        months = days // 30
        return "a month ago" if months == 1 else f"about {months} months ago"
    years = days // 365
    return "a year ago" if years == 1 else f"over {years} years ago"


@dataclass(frozen=True)
class Recollection:
    """One remembered encounter."""

    when: date
    text: str

    def render(self, now: date) -> str:
        return f"{elapsed(self.when, now)}: {self.text}"


class MemoryStore:
    """Per-save, per-character recollections."""

    def __init__(
        self,
        root: Path | None = None,
        limit: int = MAX_ENTRIES_PER_CHARACTER,
    ) -> None:
        self._root = Path(root) if root else None
        self._limit = limit
        # save id -> character -> entries
        self._saves: dict[str, dict[str, list[Recollection]]] = {}
        self._loaded: set[str] = set()

    # --- recall and record ----------------------------------------------

    def remember(
        self, save_id: str, character: str, when: date, text: str
    ) -> None:
        text = " ".join((text or "").split())
        if not text:
            return

        entries = self._entries(save_id, character)
        if any(e.text == text for e in entries):
            return          # the same beat twice is not a second memory

        entries.append(Recollection(when, text))
        entries.sort(key=lambda e: e.when)
        del entries[: max(0, len(entries) - self._limit)]
        self._persist(save_id)

    def recall(
        self, save_id: str, character: str, now: date
    ) -> tuple[str, ...]:
        """What this character may recall, given where the story now is.

        Anything dated after `now` is discarded rather than hidden: the player
        has loaded an earlier save, and that future did not happen.
        """
        entries = self._entries(save_id, character)
        if not entries:
            return ()

        kept = [e for e in entries if e.when <= now]
        if len(kept) != len(entries):
            self._saves[save_id][character] = kept
            self._persist(save_id)
        return tuple(e.render(now) for e in kept)

    def forget(self, save_id: str, character: str) -> None:
        self._saves.get(save_id, {}).pop(character, None)
        self._persist(save_id)

    def __len__(self) -> int:
        return sum(
            len(v) for save in self._saves.values() for v in save.values()
        )

    # --- storage ---------------------------------------------------------

    def _entries(self, save_id: str, character: str) -> list[Recollection]:
        self._load(save_id)
        return self._saves.setdefault(save_id, {}).setdefault(character, [])

    def _path(self, save_id: str) -> Path | None:
        if self._root is None:
            return None
        # Save ids come from the game as a slot number, but sanitise anyway:
        # this becomes a filename.
        safe = "".join(c for c in save_id if c.isalnum() or c in "-_") or "unknown"
        return self._root / f"{safe}.json"

    def _load(self, save_id: str) -> None:
        if save_id in self._loaded:
            return
        self._loaded.add(save_id)

        path = self._path(save_id)
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return          # a corrupt store is forgotten, never fatal

        characters: dict[str, list[Recollection]] = {}
        for character, entries in (raw.get("characters") or {}).items():
            kept: list[Recollection] = []
            for entry in entries:
                try:
                    kept.append(Recollection(
                        date.fromisoformat(entry["when"]), str(entry["text"])
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            if kept:
                characters[character] = kept[-self._limit:]
        self._saves.setdefault(save_id, {}).update(characters)

    def _persist(self, save_id: str) -> None:
        path = self._path(save_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "characters": {
                    character: [
                        {"when": e.when.isoformat(), "text": e.text}
                        for e in entries
                    ]
                    for character, entries in self._saves.get(save_id, {}).items()
                    if entries
                }
            }
            path.write_text(
                json.dumps(payload, indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass            # memory is a nicety; losing it must not end a turn
