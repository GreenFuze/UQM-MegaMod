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

    # Members inside a #if 0 block are not compiled, so the game's enum does
    # not contain them and every later phrase sits three indices lower than a
    # naive read suggests. pkunk/strings.h does exactly this for
    # NOT_CONQUER_10..12, and counting them misattributes every line after
    # that point - which is the failure this class exists to prevent.
    _IF_ZERO = re.compile(r"^\s*#\s*if\s+0\s*$")
    _IF_ANY = re.compile(r"^\s*#\s*if")
    _ENDIF = re.compile(r"^\s*#\s*endif\b")
    _DIRECTIVE = re.compile(r"^\s*#")

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
        skip_depth = 0

        for line in match.group("body").splitlines():
            # Drop comments before matching so annotated members still parse.
            line = re.sub(r"/\*.*?\*/", "", line)

            # Inside a disabled block, track nesting and look only for its end.
            if skip_depth:
                if self._IF_ANY.match(line):
                    skip_depth += 1
                elif self._ENDIF.match(line):
                    skip_depth -= 1
                continue

            if self._IF_ZERO.match(line):
                skip_depth = 1
                continue
            if self._ENDIF.match(line):
                continue
            if self._DIRECTIVE.match(line):
                # Any other conditional means we cannot know what the compiler
                # emitted, and guessing shifts every later index. Refuse rather
                # than produce a table that is quietly wrong.
                raise PhraseTableError(
                    f"{self._path}: unsupported preprocessor directive inside "
                    f"enum: {line.strip()!r}"
                )

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
    # None when the enum declares a phrase the dialogue file does not carry.
    # The ref still resolves to a name, but there are no words to quote.
    text: str | None
    has_interpolation: bool


class PhraseTable:
    """A race's phrases, joined from strings.h and the dialogue file.

    Construction verifies that the two sources describe the same phrases in
    the same order; a mismatch is fatal rather than silently producing a
    misaligned table, since a shifted index would attribute the wrong words
    to the wrong action.
    """

    def __init__(self, header: StringsHeader, dialogue: DialogueFile,
            aliases: dict[int, str] | None = None) -> None:
        self._entries = self._join(header, dialogue, dict(aliases or {}))

    @property
    def entries(self) -> tuple[TableEntry, ...]:
        return self._entries

    def by_key(self, key: str) -> TableEntry:
        for entry in self._entries:
            if entry.key == key:
                return entry
        raise KeyError(f"no phrase {key!r} in table")

    @staticmethod
    def _join(header: StringsHeader, dialogue: DialogueFile,
            aliases: dict[int, str]) -> tuple[TableEntry, ...]:
        # Enum index 0 is NULL_PHRASE and has no dialogue entry; real phrases
        # start at enum value 1 == dialogue entry 0.
        enum_names = header.names[1:]
        phrases: tuple[Phrase, ...] = dialogue.phrases

        # A dialogue file LONGER than the enum means an entry was inserted, and
        # an insertion shifts every index after it. Still fatal.
        if len(phrases) > len(enum_names):
            raise PhraseTableError(
                f"{dialogue.path.name} has {len(phrases)} phrases but the enum "
                f"has {len(enum_names)} - the dialogue file is longer than the "
                f"enum, so an entry was inserted and the tables are misaligned"
            )

        entries: list[TableEntry] = []
        for offset, name in enumerate(enum_names):
            if offset >= len(phrases):
                # A trailing enum member the dialogue file does not carry. umgah
                # declares OUT_TAKES and umgahc.c:511 speaks it, yet umgah.txt
                # has no entry - the stock game reads off the end of its own
                # table. A missing tail cannot shift an earlier index, so carry
                # the name with no text rather than failing the whole race.
                entries.append(
                    TableEntry(
                        enum_value=offset + 1,
                        key=name,
                        kind=(PhraseKind.NPC if name.isupper()
                                else PhraseKind.PLAYER),
                        voice_clip=None,
                        text=None,
                        has_interpolation=False,
                    )
                )
                continue

            phrase = phrases[offset]

            # Position is what the game dispatches on - commglue.h resolves a
            # phrase as SetAbsStringTableIndex (..., R - 1) - and the name is a
            # consistency check on top. An explicit per-index alias covers a
            # known rename (MegaMod's artifact randomisation renames starbase
            # indices 151 and 152) while a genuine shift still fails, because a
            # shift mismatches in a long run nobody would alias by hand.
            if name != phrase.key and aliases.get(offset) != phrase.key:
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
