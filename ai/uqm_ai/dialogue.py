"""Parser for UQM conversation resource files (content/base/comm/<race>/<race>.txt).

The game loads these as a string table indexed positionally; the C enum in
src/uqm/comm/<race>/strings.h names each index. The file itself carries the
phrase key in a comment marker, which lets us recover the mapping without
parsing C.

Format:

    #(PHRASE_KEY)\tvoice-clip.ogg     <- marker; the clip is optional
    first line of the phrase
    second line of the phrase
                                      <- blank line ends the phrase

Uppercase keys are NPC lines and are the only ones that carry voice clips;
lowercase keys are player response options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PhraseKind(Enum):
    """Whether a phrase is spoken by the NPC or offered to the player."""

    NPC = "npc"
    PLAYER = "player"


@dataclass(frozen=True)
class Phrase:
    """One entry in a conversation resource file."""

    index: int
    key: str
    kind: PhraseKind
    voice_clip: str | None
    lines: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        """The phrase as a single string, as the player would read it."""
        return " ".join(self.lines)

    @property
    def has_interpolation(self) -> bool:
        """True if the text contains a Lua <% ... %> interpolation."""
        return "<%" in self.text


class DialogueParseError(Exception):
    """Raised when a conversation file cannot be parsed.

    Carries the file and line number so a malformed resource is diagnosable
    rather than silently yielding a short phrase list.
    """

    def __init__(self, path: Path, line_no: int, message: str) -> None:
        super().__init__(f"{path}:{line_no}: {message}")
        self.path = path
        self.line_no = line_no


class DialogueFile:
    """A parsed conversation resource file.

    Acquires and parses the file in the constructor; the instance is usable
    immediately or construction fails.
    """

    # "#(KEY)" optionally followed by whitespace and a voice clip filename.
    _MARKER = re.compile(r"^#\((?P<key>[A-Za-z0-9_]+)\)(?:\s+(?P<clip>\S+))?\s*$")

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(f"dialogue file not found: {self._path}")
        self._phrases: tuple[Phrase, ...] = self._parse()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def phrases(self) -> tuple[Phrase, ...]:
        return self._phrases

    def by_key(self, key: str) -> Phrase:
        """Look up a phrase by its key, failing loudly if absent."""
        for phrase in self._phrases:
            if phrase.key == key:
                return phrase
        raise KeyError(f"no phrase {key!r} in {self._path}")

    def of_kind(self, kind: PhraseKind) -> tuple[Phrase, ...]:
        return tuple(p for p in self._phrases if p.kind is kind)

    def _parse(self) -> tuple[Phrase, ...]:
        text = self._path.read_text(encoding="utf-8", errors="strict")

        # Walk the file accumulating lines into the phrase opened by the last
        # marker. A blank line closes the current phrase's text but does not
        # itself start a new one.
        phrases: list[Phrase] = []
        current: dict | None = None
        pending: list[str] = []

        def close_current() -> None:
            if current is not None:
                phrases.append(
                    Phrase(
                        index=len(phrases),
                        key=current["key"],
                        kind=current["kind"],
                        voice_clip=current["clip"],
                        lines=tuple(pending),
                    )
                )

        for line_no, raw in enumerate(text.splitlines(), start=1):
            marker = self._MARKER.match(raw)
            if marker:
                close_current()
                pending = []
                key = marker.group("key")
                current = {
                    "key": key,
                    # The uppercase/lowercase convention is load-bearing in the
                    # game's own enum, so we trust it rather than guessing.
                    "kind": PhraseKind.NPC if key.isupper() else PhraseKind.PLAYER,
                    "clip": marker.group("clip"),
                }
                continue

            stripped = raw.strip()
            if not stripped:
                continue
            if current is None:
                raise DialogueParseError(
                    self._path, line_no, f"text before any #(KEY) marker: {stripped!r}"
                )
            pending.append(stripped)

        close_current()

        if not phrases:
            raise DialogueParseError(self._path, 0, "no phrases found")
        return tuple(phrases)

    def __len__(self) -> int:
        return len(self._phrases)

    def __repr__(self) -> str:
        return f"<DialogueFile {self._path.name}: {len(self._phrases)} phrases>"
