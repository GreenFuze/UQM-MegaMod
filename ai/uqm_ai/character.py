"""Authored character data: who someone is, and what they may know when.

One TOML file per character under ai/characters/. TOML because tomllib is
stdlib in 3.12 and read-only by construction, so this adds no dependency and
nothing in a character file can execute.

A file is the single source of truth for one character - persona, the phrases
the story has unlocked, wider canon, and the things they know they do not know.

Loading is strict. An unrecognised key, a duplicate id, a malformed condition
or a flag the game does not have is fatal here, when the sidecar starts, rather
than mid-conversation. The whole point of the gate is that it is predictable.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

from .precond import Node, PreconditionError, parse

_FILE_KEYS = frozenset({
    "dialogue", "name", "species", "persona", "visits_flag", "voice_clip",
    "aliases", "knowledge", "lore", "denial", "reply_length", "register",
})
_KNOWLEDGE_KEYS = frozenset({"id", "when", "phrases", "fact", "supersedes"})
_LORE_KEYS = frozenset({"id", "when", "text", "source", "true_from", "true_until"})
_DENIAL_KEYS = frozenset({"topic", "unless", "note"})


class CharacterError(Exception):
    """Raised when a character file is malformed."""


@dataclass(frozen=True)
class KnowledgeItem:
    """Canonical phrases the story has unlocked for this character.

    `phrases` are keys from the character's own dialogue. They are what the
    character may draw on before having said them this conversation - the
    difference between "he can repeat what he just told you" and "he knows
    this, and will mention it if asked".
    """

    id: str
    when: Node
    phrases: tuple[str, ...] = ()
    fact: str | None = None
    supersedes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LoreItem:
    """Wider canon with no shipped phrase behind it.

    `source` is how THIS character came to know it, and is required. Without it
    the model invents a chain of custody, and the invented chain is itself a
    spoiler - "the Melnorme told us" when the player has never met them.
    """

    id: str
    when: Node
    text: str
    source: str


@dataclass(frozen=True)
class Denial:
    """Something the character knows it does not know.

    Authored, because otherwise the model improvises ignorance differently
    every turn - sometimes a blank stare, sometimes a suspiciously
    well-informed hedge. A consistent wall is what makes interrogation feel
    like talking to a person with limits.

    `unless` lifts the denial once the character genuinely does know.
    """

    topic: str
    note: str
    unless: Node | None = None


@dataclass(frozen=True)
class CharacterProfile:
    """The authored description of a character, and its knowledge gates."""

    key: str
    name: str
    species: str
    description: str
    dialogue_res: str = ""
    # How long a reply should be, in this character's own terms. A chat
    # model left to itself writes an essay; a game character does not.
    # Per character rather than global, because Fwiffo's verbosity is the
    # joke and the Ur-Quan's brevity is the threat.
    reply_length: str = "two to four sentences"
    # When and how this character swears, in their own idiom. The game's
    # own text already reaches for "damn" and "hell"; this makes that
    # register available where the fiction earns it, and nowhere else.
    register: str | None = None
    visits_flag: str | None = None
    voice_clip: str | None = None
    aliases: Mapping[int, str] = field(default_factory=dict)
    knowledge: tuple[KnowledgeItem, ...] = ()
    lore: tuple[LoreItem, ...] = ()
    denials: tuple[Denial, ...] = ()

    def render(self) -> str:
        return f"You are {self.name}, a {self.species}.\n\n{self.description.strip()}"

    # --- per-turn gating -------------------------------------------------

    def permitted_phrases(
        self, state: Mapping[str, int], today: date
    ) -> tuple[str, ...]:
        """Canonical phrase keys the story has unlocked, in authored order."""
        live = self._live_knowledge(state, today)
        keys: list[str] = []
        for item in live:
            for key in item.phrases:
                if key not in keys:
                    keys.append(key)
        return tuple(keys)

    def permitted_facts(
        self, state: Mapping[str, int], today: date
    ) -> tuple[str, ...]:
        return tuple(
            item.fact for item in self._live_knowledge(state, today) if item.fact
        )

    def permitted_lore(
        self, state: Mapping[str, int], today: date
    ) -> tuple[LoreItem, ...]:
        return tuple(
            item for item in self.lore if item.when.evaluate(state, today)
        )

    def active_denials(
        self, state: Mapping[str, int], today: date
    ) -> tuple[Denial, ...]:
        return tuple(
            d for d in self.denials
            if d.unless is None or not d.unless.evaluate(state, today)
        )

    def _live_knowledge(
        self, state: Mapping[str, int], today: date
    ) -> tuple[KnowledgeItem, ...]:
        live = [i for i in self.knowledge if i.when.evaluate(state, today)]

        # Learning a big thing retires smaller ones. The game does this itself:
        # zoqfotc.c sets KnowMask = KNOW_ALL when KOHR_AH_FRENZY fires, because
        # once you know the Kohr-Ah are exterminating everyone the earlier war
        # bulletins are moot.
        retired: set[str] = set()
        for item in live:
            retired |= item.supersedes
        return tuple(i for i in live if i.id not in retired)

    def flags(self) -> frozenset[str]:
        """Every game-state flag any condition in this file names."""
        names: frozenset[str] = frozenset()
        for item in self.knowledge:
            names |= item.when.flags()
        for item in self.lore:
            names |= item.when.flags()
        for denial in self.denials:
            if denial.unless is not None:
                names |= denial.unless.flags()
        if self.visits_flag:
            names |= {self.visits_flag}
        return names


# --- loading -------------------------------------------------------------


def _check_keys(where: str, got: Mapping[str, object], allowed: frozenset[str]) -> None:
    unknown = sorted(set(got) - allowed)
    if unknown:
        raise CharacterError(
            f"{where}: unrecognised key(s) {unknown}; allowed are "
            f"{sorted(allowed)}"
        )


def _condition(where: str, raw: object) -> Node:
    try:
        return parse(raw)  # type: ignore[arg-type]
    except PreconditionError as exc:
        raise CharacterError(f"{where}: {exc}") from exc


def _date_bound(where: str, raw: object, op: str) -> str:
    if isinstance(raw, date):
        return f"date {op} {raw.isoformat()}"
    if isinstance(raw, str):
        return f"date {op} {raw}"
    raise CharacterError(f"{where}: expected a date, got {raw!r}")


def load_character(path: Path, known_flags: frozenset[str] | None = None) -> CharacterProfile:
    """Read one character file. Raises CharacterError on anything malformed."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CharacterError(f"{path}: {exc}") from exc

    _check_keys(str(path), raw, _FILE_KEYS)

    for required in ("dialogue", "name", "species", "persona"):
        if not raw.get(required):
            raise CharacterError(f"{path}: missing required key {required!r}")

    knowledge = _load_knowledge(path, raw.get("knowledge", []))
    lore = _load_lore(path, raw.get("lore", []))
    denials = _load_denials(path, raw.get("denial", []))

    aliases = {}
    for index, key in (raw.get("aliases") or {}).items():
        try:
            aliases[int(index)] = str(key)
        except ValueError:
            raise CharacterError(
                f"{path}: alias key {index!r} is not a phrase index"
            ) from None

    profile = CharacterProfile(
        key=path.stem,
        name=raw["name"],
        species=raw["species"],
        description=raw["persona"],
        dialogue_res=raw["dialogue"],
        reply_length=(raw.get("reply_length")
                or "two to four sentences"),
        register=raw.get("register") or None,
        visits_flag=raw.get("visits_flag") or None,
        voice_clip=raw.get("voice_clip") or None,
        aliases=aliases,
        knowledge=knowledge,
        lore=lore,
        denials=denials,
    )

    if known_flags is not None:
        # A flag the game does not have reads as 0 forever, so the item never
        # unlocks and the failure is invisible in play.
        unknown = sorted(profile.flags() - known_flags)
        if unknown:
            raise CharacterError(
                f"{path}: condition names game state flag(s) the game does not "
                f"have: {unknown}"
            )

    return profile


def _load_knowledge(path: Path, entries: object) -> tuple[KnowledgeItem, ...]:
    if not isinstance(entries, list):
        raise CharacterError(f"{path}: [[knowledge]] must be a list")

    items: list[KnowledgeItem] = []
    seen: set[str] = set()
    for entry in entries:
        where = f"{path}: knowledge {entry.get('id', '?')!r}"
        _check_keys(where, entry, _KNOWLEDGE_KEYS)

        item_id = entry.get("id")
        if not item_id:
            raise CharacterError(f"{path}: a [[knowledge]] entry has no id")
        if item_id in seen:
            raise CharacterError(f"{path}: duplicate knowledge id {item_id!r}")
        seen.add(item_id)

        phrases = tuple(entry.get("phrases", ()))
        if not phrases and not entry.get("fact"):
            raise CharacterError(
                f"{where}: needs phrases, a fact, or both - otherwise it "
                f"unlocks nothing"
            )

        items.append(KnowledgeItem(
            id=item_id,
            when=_condition(where, entry.get("when", "always")),
            phrases=phrases,
            fact=entry.get("fact"),
            supersedes=frozenset(entry.get("supersedes", ())),
        ))

    known = {i.id for i in items}
    for item in items:
        missing = sorted(item.supersedes - known)
        if missing:
            raise CharacterError(
                f"{path}: knowledge {item.id!r} supersedes unknown id(s) {missing}"
            )
    return tuple(items)


def _load_lore(path: Path, entries: object) -> tuple[LoreItem, ...]:
    if not isinstance(entries, list):
        raise CharacterError(f"{path}: [[lore]] must be a list")

    items: list[LoreItem] = []
    seen: set[str] = set()
    for entry in entries:
        where = f"{path}: lore {entry.get('id', '?')!r}"
        _check_keys(where, entry, _LORE_KEYS)

        item_id = entry.get("id")
        if not item_id:
            raise CharacterError(f"{path}: a [[lore]] entry has no id")
        if item_id in seen:
            raise CharacterError(f"{path}: duplicate lore id {item_id!r}")
        seen.add(item_id)

        if not entry.get("text"):
            raise CharacterError(f"{where}: has no text")
        if not entry.get("source"):
            raise CharacterError(
                f"{where}: has no source. Every lore item must record how this "
                f"character came to know it, or the model will invent a chain "
                f"of custody that is itself a spoiler"
            )

        # Whether a fact is TRUE yet and whether this character could KNOW it
        # are different questions; true_from/true_until answer the first and
        # `when` answers the second. They are conjoined here.
        conditions: list[str] = []
        when = entry.get("when", "always")
        if isinstance(when, (list, tuple)):
            conditions.extend(str(c) for c in when)
        else:
            conditions.append(str(when))
        if entry.get("true_from") is not None:
            conditions.append(_date_bound(where, entry["true_from"], ">="))
        if entry.get("true_until") is not None:
            conditions.append(_date_bound(where, entry["true_until"], "<"))

        items.append(LoreItem(
            id=item_id,
            when=_condition(where, conditions),
            text=entry["text"],
            source=entry["source"],
        ))
    return tuple(items)


def _load_denials(path: Path, entries: object) -> tuple[Denial, ...]:
    if not isinstance(entries, list):
        raise CharacterError(f"{path}: [[denial]] must be a list")

    denials: list[Denial] = []
    for entry in entries:
        where = f"{path}: denial {entry.get('topic', '?')!r}"
        _check_keys(where, entry, _DENIAL_KEYS)
        if not entry.get("topic"):
            raise CharacterError(f"{path}: a [[denial]] entry has no topic")
        if not entry.get("note"):
            raise CharacterError(f"{where}: has no note")
        unless = entry.get("unless")
        denials.append(Denial(
            topic=entry["topic"],
            note=entry["note"],
            unless=_condition(where, unless) if unless else None,
        ))
    return tuple(denials)
