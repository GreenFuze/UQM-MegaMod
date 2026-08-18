"""Deterministic mock provider.

Exists to prove the whole game integration with no model, no network and no
GPU: the same input always yields the same action and the same prose, so the
integration tests assert on behaviour rather than on a language model's mood.

It is deliberately crude. Its job is to exercise the contract, not to be good.
"""

from __future__ import annotations

import re

from ..protocol import ConverseRequest, ConverseResponse
from .base import LLMProvider

# Player intent -> the action id it should select, when that action is
# actually on offer this turn. Order matters: the first match wins.
_INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("join_us", re.compile(r"\b(join|come with|recruit|aboard|crew up)\b", re.I)),
    ("changed_mind", re.compile(r"\b(never ?mind|forget it|goodbye|bye|leave)\b", re.I)),
    ("die_slugboy", re.compile(r"\b(die|kill you|destroy you|attack)\b", re.I)),
    ("where_are_urquan", re.compile(r"\bur-?quan\b", re.I)),
    ("what_about_yourself", re.compile(r"\b(who are you|yourself|about you)\b", re.I)),
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

_DEFAULT_PROSE = (
    "I am not at all certain I should be discussing this. But since you asked "
    "so politely, and since you have all those weapons pointed at me, I "
    "suppose a small amount of cooperation could not hurt."
)


class MockProvider(LLMProvider):
    """Rule-based provider with fully deterministic output."""

    @property
    def name(self) -> str:
        return "mock"

    def generate(self, request: ConverseRequest, system_prompt: str) -> ConverseResponse:
        chosen = self._select_action(request)
        key = chosen.key if chosen else None
        spoken = _PROSE.get(key, _DEFAULT_PROSE) if key else _DEFAULT_PROSE

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
