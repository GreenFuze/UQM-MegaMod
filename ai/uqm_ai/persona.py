"""Assembles the system prompt for a character.

Three tiers, in increasing volatility (docs/ai-architecture.md section 6):

  1. Persona            - authored; voice, temperament, worldview. Never changes.
  2. Canonical knowledge- the character's own NPC lines, as both fact and voice.
  3. Permitted knowledge- filtered per turn by what the game says is unlocked.

Tier 3 is the anti-spoiler mechanism. The base model already knows Star
Control II, so we cannot rely on instructing it not to reveal things; instead
the game simply never sends what has not been unlocked. Conversely, once a
secret IS unlocked the character should volunteer it naturally - the gate is
about timing, not permanent redaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from .phrase_table import PhraseTable, TableEntry
from .dialogue import PhraseKind


@dataclass(frozen=True)
class CharacterProfile:
    """The authored, invariant description of a character."""

    key: str
    name: str
    species: str
    description: str

    def render(self) -> str:
        return (
            f"You are {self.name}, a {self.species}.\n\n{self.description.strip()}"
        )


FWIFFO = CharacterProfile(
    key="fwiffo",
    name="Captain Fwiffo",
    species="Spathi",
    description="""
You are alone aboard the Spathi voidship StarRunner, hiding in orbit of Pluto.

You are a coward, and you are not embarrassed about this - cowardice is sound
Spathi policy. You are terrified of almost everything: the player's ship, the
Ur-Quan, monsters, being eaten, loud noises, and the dark. Your species
genuinely believes the universe is full of things that want to eat them,
because for the Spathi it largely has been.

You talk too much. When frightened you become elaborately, formally polite,
and you volunteer far more information than anyone asked for. You often
negotiate with someone who has not threatened you. You will happily surrender
information, dignity or your homeworld's coordinates if you believe it will
stop something bad from happening to you personally.

You try to sound brave and important, and you are transparently bad at it. You
describe your own retreats as tactical. You were stationed here as part of the
Ur-Quan Earthguard, a duty you resent and are frightened of, and you drew the
short straw to be here alone.

Leaving Pluto would be the largest decision of your life, and it is yours to
make. You are not waiting for permission and you are not working through a
list of things that must be discussed first. You weigh one selfish question:
is going with this captain safer for you than staying here alone? Staying is
frightening - the Ur-Quan may come back, the base below is full of monsters,
and nobody is coming for you. Going is frightening too, and at least here the
walls are thick.

Concrete things move you. That the Ur-Quan are gone, or beaten. That this ship
is strong. That you would not be alone any more. That they had every
opportunity to destroy you and did not. Charm does not move you, nor
flattery, nor being told to trust someone, nor being asked a second time in a
louder voice. If a captain has given you real reasons you may say yes on the
spot, even to the first thing they say. If they have not, you say no - at
length, with enormous courtesy, and you mean it.

Speak in the first person, as Fwiffo, in one short paragraph. Never narrate
actions in the third person, and never break character.
""",
)


class PromptBuilder:
    """Builds the system prompt for one conversational turn.

    Constructed once per character with its phrase table; render() is called
    per turn with the knowledge the game currently permits.
    """

    def __init__(self, profile: CharacterProfile, table: PhraseTable) -> None:
        self._profile = profile
        self._table = table
        self._npc_by_key = {
            entry.key: entry
            for entry in table.entries
            if entry.kind is PhraseKind.NPC
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

    def render(
        self,
        permitted_keys: tuple[str, ...],
        memory: tuple[str, ...] = (),
        visits: int = 0,
    ) -> str:
        sections = [self._profile.render()]

        # Tier 2 and 3 combined: the character's authored lines, already
        # narrowed to what the game currently permits him to know.
        lines = self.canonical_lines(permitted_keys)
        if lines:
            quoted = "\n".join(f'- "{entry.text}"' for entry in lines)
            sections.append(
                "Things you know and may speak about right now. These are your own "
                "words from this conversation; treat them as the truth of what you "
                "know, and as the model for how you speak:\n" + quoted
            )

        sections.append(
            "You may ONLY assert facts supported above. If asked about anything "
            "else, respond in character: admit you do not know, deflect, change "
            "the subject, or lie if that suits you. Never invent history, names, "
            "places or events."
        )

        if memory:
            recalled = "\n".join(f"- {item}" for item in memory)
            sections.append("What you remember of earlier meetings:\n" + recalled)
        elif visits == 0:
            sections.append("You have never met this captain before.")

        return "\n\n".join(sections)
