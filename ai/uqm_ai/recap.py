"""Turns a finished conversation into one line the character will remember.

Written when the encounter ENDS rather than during it, for two reasons. A
summary of the whole meeting is more coherent than a fragment written each turn
before anyone knew where the conversation was going. And it costs a model call,
which must not be paid on the critical path: the player is walking away, and
waiting several seconds for the screen to change would be worse than having no
memory at all.

So it runs on a daemon thread. If the sidecar is shutting down the summary is
simply lost, which is the correct trade - memory is a nicety and the game
closing is not.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date

from . import gamelog

# A long visit to the starbase is a dozen exchanges. Beyond that the tail is
# what matters, and an unbounded buffer is an unbounded prompt.
MAX_TURNS = 16

# One line. Anything longer stops being a recollection and starts being a
# transcript, which is exactly what memory is not.
MAX_SUMMARY = 240


@dataclass
class Encounter:
    """What has been said this meeting, kept until it ends."""

    save_id: str
    character: str
    turns: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, player: str, spoken: str) -> None:
        self.turns.append((player, spoken))
        del self.turns[: max(0, len(self.turns) - MAX_TURNS)]

    def note(self, text: str) -> None:
        """A `remember` line the model chose to keep, as a hint to the recap."""
        if text and text not in self.notes:
            self.notes.append(text)
            del self.notes[: max(0, len(self.notes) - MAX_TURNS)]

    def transcript(self) -> str:
        lines = []
        for player, spoken in self.turns:
            lines.append(f"Captain: {' '.join(player.split())}")
            lines.append(f"You: {' '.join(spoken.split())}")
        if self.notes:
            lines.append("")
            lines.append("Things you thought worth keeping in mind:")
            lines.extend(f"- {n}" for n in self.notes)
        return "\n".join(lines)


class Recapper:
    """Summarises finished encounters off the critical path."""

    def __init__(self, llm, store, warn=None) -> None:
        self._llm = llm
        self._store = store
        self._warn = warn or (lambda message: None)

    def finish(self, encounter: Encounter, when: date) -> threading.Thread | None:
        """Summarise and file, on a background thread. Never raises."""
        if not encounter.turns:
            return None

        thread = threading.Thread(
            target=self._run,
            args=(encounter, when),
            name=f"recap-{encounter.character}",
            daemon=True,
        )
        thread.start()
        return thread

    def _run(self, encounter: Encounter, when: date) -> None:
        try:
            summary = self._summarise(encounter)
        except Exception as exc:  # noqa: BLE001 - a lost memory is not a fault
            self._warn(f"could not summarise the encounter: {exc}")
            return

        if not summary:
            return

        self._store.remember(
            encounter.save_id, encounter.character, when, summary
        )
        gamelog.emit(
            f"{encounter.character}: remembered - {summary[:120]}"
        )

    def _summarise(self, encounter: Encounter) -> str:
        prompt = (
            "You are summarising a conversation for one of its participants, "
            "so that they can recall it when the two meet again.\n\n"
            "Write ONE sentence, under 40 words, in the third person, "
            "describing what the captain wanted and how it went. Record what "
            "was actually said and done - not atmosphere, not how anyone felt "
            "about it, and never anything neither party mentioned. If nothing "
            "of consequence happened, say so plainly in a few words."
        )
        text = self._llm.summarise(prompt, encounter.transcript())
        text = " ".join((text or "").split())
        return text[:MAX_SUMMARY]
