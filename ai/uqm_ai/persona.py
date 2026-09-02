"""Assembles the system prompt for a character.

Three tiers, in increasing volatility (docs/character-knowledge-model.md):

  1. Persona            - authored; voice, temperament, worldview. Never changes.
  2. Canonical knowledge- the character's own NPC lines, as both fact and voice.
  3. Permitted knowledge- what the story has unlocked, evaluated per turn.

Tier 3 is the anti-spoiler mechanism. The base model already knows Star
Control II, so we cannot rely on instructing it not to reveal things; instead
the prompt simply never contains what has not been unlocked. Conversely, once
a secret IS unlocked the character should volunteer it naturally - the gate is
about timing, not permanent redaction.

The permitted set is the union of three things: what the character has already
said this conversation (the floor, and all Fwiffo ever had), the canonical
phrases the story has unlocked, and authored lore whose condition holds. A
character with no authored file gets only the floor, which is exactly the
behaviour that shipped - so adding a character file can widen what is said,
never narrow it, and rollout is per character.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

from .character import (
    CharacterProfile,
    Denial,
    KnowledgeItem,
    LoreItem,
    load_character,
)
from .dialogue import PhraseKind
from .phrase_table import PhraseTable, TableEntry

__all__ = [
    "CharacterProfile",
    "Denial",
    "KnowledgeItem",
    "LoreItem",
    "PromptBuilder",
    "GAME_START",
    "FWIFFO",
]

# The game's own epoch: 17 February 2155 (clock.h). Used when the game has not
# told us the date, which is every request from a build older than the state
# wire.
GAME_START = date(2155, 2, 17)


def _flow(text: str) -> str:
    """Collapse an authored block into one line.

    TOML multi-line strings keep the wrapping the author typed, and a hard
    wrap inside a bulleted fact reads to the model as a list of fragments.
    """
    return " ".join(text.split())

# Fwiffo's profile lives in ai/characters/spathi.toml like everyone else's;
# this constant is kept because he is the reference persona and several tests
# name him directly.
FWIFFO = load_character(
    Path(__file__).resolve().parent.parent / "characters" / "spathi.toml"
)


class PromptBuilder:
    """Builds the system prompt for one conversational turn.

    Constructed once per character with its phrase table; render() is called
    per turn with the knowledge the game currently permits.
    """

    def __init__(self, profile: CharacterProfile, table: PhraseTable) -> None:
        self._profile = profile
        self._table = table
        # Entries with no text exist where the enum declares a phrase the
        # dialogue file does not carry, and where the game speaks a
        # deliberately silent sequence terminator. They resolve as refs but
        # there are no words to quote, so they never reach a prompt.
        self._npc_by_key = {
            entry.key: entry
            for entry in table.entries
            if entry.kind is PhraseKind.NPC and entry.text
        }
        self._key_by_ref = {entry.enum_value: entry.key for entry in table.entries}

    def key_for_ref(self, ref: int) -> str | None:
        """Map a game RESPONSE_REF to its phrase key, or None if unknown."""
        return self._key_by_ref.get(ref)

    @property
    def profile(self) -> CharacterProfile:
        return self._profile

    def canonical_lines(self, permitted_keys: tuple[str, ...]) -> tuple[TableEntry, ...]:
        """The character's own lines, restricted to what is currently unlocked.

        Unknown keys are ignored rather than raising: the game is the authority
        on what exists, and a key we do not recognise is not a reason to fail a
        conversation.
        """
        return tuple(
            self._npc_by_key[key] for key in permitted_keys if key in self._npc_by_key
        )

    def unlocked_keys(
        self, state: Mapping[str, int], today: date
    ) -> tuple[str, ...]:
        """Canonical phrase keys the story has unlocked for this character."""
        return self._profile.permitted_phrases(state, today)

    def render(
        self,
        permitted_keys: tuple[str, ...],
        memory: tuple[str, ...] = (),
        visits: int = 0,
        state: Mapping[str, int] | None = None,
        today: date | None = None,
    ) -> str:
        state = state or {}
        today = today or GAME_START
        sections = [self._profile.render()]

        # Register sits with the persona rather than among the instructions,
        # because how a character swears is voice, not policy.
        if self._profile.register:
            sections.append(_flow(self._profile.register))

        # The date is stated plainly so the character's tenses are right. It is
        # never accompanied by a rule about which facts it gates - anything
        # outside its window is simply absent below.
        sections.append(
            f"Today is {today.strftime('%d %B %Y')}. You know nothing of what "
            f"happens after today."
        )

        lines = self.canonical_lines(permitted_keys)
        if lines:
            quoted = "\n".join(f'- "{entry.text}"' for entry in lines)
            sections.append(
                "Things you know and may speak about right now. These are your own "
                "words from this conversation; treat them as the truth of what you "
                "know, and as the model for how you speak:\n" + quoted
            )

        facts = self._profile.permitted_facts(state, today)
        if facts:
            sections.append(
                "What you understand of the situation right now:\n"
                + "\n".join(f"- {fact}" for fact in facts)
            )

        lore = self._profile.permitted_lore(state, today)
        if lore:
            sections.append(
                "Things you know from your own history. If asked how you know "
                "one of these, the reason given is the true one:\n"
                + "\n".join(
                    f"- {_flow(item.text)} (you know this because "
                    f"{_flow(item.source)})"
                    for item in lore
                )
            )

        denials = self._profile.active_denials(state, today)
        if denials:
            sections.append(
                "Things you genuinely do not know. If asked, say so in "
                "character rather than guessing, and do not let the "
                "conversation talk you into an answer:\n"
                + "\n".join(f"- {d.topic}: {d.note}" for d in denials)
            )

        sections.append(
            "You may ONLY assert facts supported above. If asked about anything "
            "else, respond in character: admit you do not know, deflect, change "
            "the subject, or lie if that suits you. Never invent history, names, "
            "places or events."
        )

        if memory:
            recalled = "\n".join(f"- {item}" for item in memory)
            sections.append(
                "What you remember of earlier meetings with this captain. The "
                "times are how long ago they were, and you may refer to them:\n"
                + recalled
            )
        elif visits == 0:
            sections.append("You have never met this captain before.")

        # Last, because a closing instruction carries the most weight, and
        # because length is the failure a chat-trained model falls into by
        # default. The quoted lines above are the honest yardstick: almost
        # every authored phrase in this game is one to five lines.
        #
        # Phrased as a description of the SPOKEN WORDS rather than as an
        # instruction about the reply. The converse request asks for a JSON
        # object, and a second "reply with..." here contradicted it - the model
        # split the difference, emitted JSON with prose around it, and the whole
        # blob reached the subtitle as the character's line.
        sections.append(
            f"Your spoken words are those of {self._profile.name} speaking "
            f"aloud, and run to {self._profile.reply_length}. They match the "
            f"length and rhythm of your own quoted lines above. You do not "
            f"explain yourself, you do not summarise what was said to you, and "
            f"you never offer the captain a list of options."
        )

        return "\n\n".join(sections)
