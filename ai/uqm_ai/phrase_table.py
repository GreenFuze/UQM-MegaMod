"""Joins a race's C phrase enum with its dialogue resource file.

The game resolves a phrase by index: commglue.h does

    SetAbsStringTableIndex (CommData.ConversationPhrases, (R - 1))

so enum value R maps to entry R-1 of the dialogue file. This module rebuilds
that mapping and verifies it, rather than assuming the two files agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .dialogue import DialogueFile, Phrase, PhraseKind


class PhraseTableError(Exception):
    """Raised when the enum and the dialogue file disagree."""


class StringsHeader:
    """The phrase enum from a race's strings.h."""

    _ENUM_BODY = re.compile(r"enum\s*\{(?P<body>.*?)\}\s*;", re.DOTALL)
    _MEMBER = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*$")

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(f"strings header not found: {self._path}")
        self._names: tuple[str, ...] = self._parse()

    @property
    def names(self) -> tuple[str, ...]:
        """Enum members in declaration order, index 0 being NULL_PHRASE."""
        return self._names

    def _parse(self) -> tuple[str, ...]:
        source = self._path.read_text(encoding="utf-8", errors="strict")
        match = self._ENUM_BODY.search(source)
        if not match:
            raise PhraseTableError(f"no enum found in {self._path}")

        names: list[str] = []
        for line in match.group("body").splitlines():
            # Drop comments before matching so annotated members still parse.
            line = re.sub(r"/\*.*?\*/", "", line)
            member = self._MEMBER.match(line)
            if member:
                names.append(member.group(1))

        if not names:
            raise PhraseTableError(f"enum in {self._path} has no members")
        return tuple(names)


@dataclass(frozen=True)
class TableEntry:
    """One phrase, joined to its enum value."""

    enum_value: int
    key: str
    kind: PhraseKind
    voice_clip: str | None
    text: str
    has_interpolation: bool


class PhraseTable:
    """A race's phrases, joined from strings.h and the dialogue file.

    Construction verifies that the two sources describe the same phrases in
    the same order; a mismatch is fatal rather than silently producing a
    misaligned table, since a shifted index would attribute the wrong words
    to the wrong action.
    """

    def __init__(self, header: StringsHeader, dialogue: DialogueFile) -> None:
        self._entries = self._join(header, dialogue)

    @property
    def entries(self) -> tuple[TableEntry, ...]:
        return self._entries

    def by_key(self, key: str) -> TableEntry:
        for entry in self._entries:
            if entry.key == key:
                return entry
        raise KeyError(f"no phrase {key!r} in table")

    @staticmethod
    def _join(header: StringsHeader, dialogue: DialogueFile) -> tuple[TableEntry, ...]:
        # Enum index 0 is NULL_PHRASE and has no dialogue entry; real phrases
        # start at enum value 1 == dialogue entry 0.
        enum_names = header.names[1:]
        phrases: tuple[Phrase, ...] = dialogue.phrases

        if len(enum_names) != len(phrases):
            raise PhraseTableError(
                f"enum has {len(enum_names)} phrases but {dialogue.path.name} "
                f"has {len(phrases)}"
            )

        entries: list[TableEntry] = []
        for offset, (name, phrase) in enumerate(zip(enum_names, phrases)):
            if name != phrase.key:
                raise PhraseTableError(
                    f"index {offset}: enum says {name!r} but dialogue says "
                    f"{phrase.key!r} - the tables are misaligned"
                )
            entries.append(
                TableEntry(
                    enum_value=offset + 1,
                    key=name,
                    kind=phrase.kind,
                    voice_clip=phrase.voice_clip,
                    text=phrase.text,
                    has_interpolation=phrase.has_interpolation,
                )
            )
        return tuple(entries)

    def __len__(self) -> int:
        return len(self._entries)
