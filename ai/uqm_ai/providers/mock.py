"""Deterministic mock provider.

Exists to prove the whole game integration with no model, no network and no
GPU: the same input always yields the same action and the same prose, so the
integration tests assert on behaviour rather than on a language model's mood.

It is deliberately crude. Its job is to exercise the contract, not to be good.
"""

from __future__ import annotations

import re
import zlib

from ..protocol import ConverseRequest, ConverseResponse, NarrateRequest
from .base import LLMProvider

# Player intent -> the action id it should select, when that action is
# actually on offer this turn. Order matters: the first match wins.
_INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Opening exchange. Only these four are exported on the first turn, so
    # without them nothing the player types can do anything at all.
    ("identify", re.compile(r"\b(identify|who are you|name yourself)\b", re.I)),
    ("hi_there", re.compile(r"\b(hi|hello|greetings|peace|friend|no harm)\b", re.I)),
    ("dont_kill", re.compile(r"\b(don't kill|do not kill|spare|mercy|please)\b", re.I)),
    ("we_fight_1", re.compile(r"\b(fight|surrender|prepare to)\b", re.I)),
    # Later in the conversation.
    ("join_us", re.compile(r"\b(join|come with|recruit|aboard)\b", re.I)),
    ("changed_mind", re.compile(r"\b(never ?mind|forget it|goodbye|bye)\b", re.I)),
    ("die_slugboy", re.compile(r"\b(die|kill you|destroy you|attack)\b", re.I)),
    ("where_are_urquan", re.compile(r"\bur-?quan\b", re.I)),
    ("what_about_yourself", re.compile(r"\b(yourself|about you)\b", re.I)),
    ("what_doing_on_pluto_1", re.compile(r"\b(pluto|doing here|why are you here)\b", re.I)),
)

_PROSE = {
    "join_us": (
        "You mean... actually leave Pluto? With YOU? In your enormous, "
        "heavily-armed, extremely conspicuous vessel? Well. I suppose it is "
        "marginally safer than remaining here alone. Do not make me regret this!"
    ),
    "changed_mind": (
        "Oh! You are leaving? What a relief! I mean - what a shame. Goodbye "
        "forever, and please do not tell anyone I am here."
    ),
    "die_slugboy": (
        "I KNEW IT! I knew you were going to say that! Very well, I shall "
        "defend myself, briefly, and then flee!"
    ),
}

# When nothing matches, the turn is pure conversation. Vary the reply by the
# input so a correct "no action taken" does not look like a stuck program --
# during testing an identical line every turn reads as a failure.
_IDLE_PROSE = (
    "I am not at all certain I should be discussing this. But since you asked "
    "so politely, and since you have all those weapons pointed at me, I "
    "suppose a small amount of cooperation could not hurt.",
    "That is a very forward question! Spathi do not generally answer forward "
    "questions, on the grounds that the asker may be sizing us up for a meal.",
    "Hmm. I shall have to think about that, at length, somewhere safe. Which "
    "is to say: not here, and preferably not while you are looking at me.",
    "You know, for an enormous heavily-armed alien vessel, you ask surprisingly "
    "conversational questions. I find that deeply suspicious and mildly nice.",
)


def _idle_prose(player_input: str) -> str:
    """Deterministic, but different for different inputs."""
    digest = zlib.crc32(player_input.strip().lower().encode("utf-8"))
    return _IDLE_PROSE[digest % len(_IDLE_PROSE)]

_PROSE.update(
    {
        "identify": (
            "Please do not shoot! I am Captain Fwiffo of the Spathi voidship "
            "StarRunner, and I surrender preemptively, as is our custom."
        ),
        "hi_there": (
            "Peace? PEACE? Oh, thank goodness. Although that is precisely what "
            "something planning to eat me would say. But I choose to believe you!"
        ),
        "dont_kill": (
            "You will not kill me? How refreshingly civilised. I had prepared a "
            "small speech for the occasion, but I shall save it for later."
        ),
        "we_fight_1": (
            "Fight? FIGHT? I must inform you that I am extremely bad at that, "
            "and I shall be fleeing at my earliest convenience!"
        ),
    }
)


class MockProvider(LLMProvider):
    """Rule-based provider with fully deterministic output."""

    @property
    def name(self) -> str:
        return "mock"

    def generate(self, request: ConverseRequest, system_prompt: str) -> ConverseResponse:
        chosen = self._select_action(request)
        key = chosen.key if chosen else None
        spoken = _PROSE.get(key) if key else None
        if spoken is None:
            spoken = _idle_prose(request.player_input)

        # Only record a memory when something actually happened, so the mock
        # does not fill memory with noise on every idle exchange.
        remember = None
        if key:
            remember = f"Player chose {key}; Fwiffo responded in character."

        return ConverseResponse(
            id=request.id,
            spoken_text=spoken,
            action=chosen.ref if chosen else None,
            remember=remember,
        )

    def summarise(self, system_prompt: str, transcript: str) -> str:
        # Deterministic, so a test can assert what was remembered.
        del system_prompt
        exchanges = transcript.count("Captain: ")
        return f"The captain spoke with them {exchanges} times."

    def narrate(self, request: NarrateRequest, system_prompt: str) -> str:
        """Return the authored answer unchanged.

        Rewording is the model's job and cannot be faked deterministically.
        Returning it verbatim is the correct degenerate case, and it lets the
        integration tests assert that the encounter's outcome - not an
        invented one - is what reaches the player.
        """
        return request.authored_text

    @staticmethod
    def _select_action(request: ConverseRequest):
        """Match intent, but only against actions the encounter exported.

        An intent whose action is not on offer yields None rather than a
        substitute, mirroring how the real provider must fail closed.
        """
        for key, pattern in _INTENT_RULES:
            candidate = request.by_key(key)
            if candidate is not None and pattern.search(request.player_input):
                return candidate
        return None
