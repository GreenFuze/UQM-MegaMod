"""Reduces MegaMod's <% ... %> interpolations to plain prose.

The dialogue files carry Lua expressions where a star, a constellation, the
captain's name or the ship's name belongs, so the text stays truthful when
MegaMod reseeds the star map. 222 phrases have one. The game evaluates them;
the sidecar reads the same files raw, so it must reduce them itself.

Two reasons this is not cosmetic.

First, the arguments leak. The second argument is an internal lookup key, and
it is often the name of the thing being hidden:

    <% comm.getConstellation("Vulpeculae", "taalo protector") %>

Left alone, that puts "taalo protector" into the prompt of a character who has
never heard of the Taalo, on a fresh game. The spoiler gate cannot see it,
because it is inside a phrase the character is legitimately allowed to speak.

Second, raw template syntax reaching a language model is an invitation to echo
it or to invent a replacement.

The reduction is the canonical 1992 value, which is the first string argument
in every one of these functions. Under StarSeed that value is WRONG - the map
has been reseeded - so a reduced phrase is meaning-only, and its specifics must
never be quoted as fact. TableEntry.has_interpolation marks exactly those.
"""

from __future__ import annotations

import re

# <% anything %>
_BLOCK = re.compile(r"<%(?P<body>.*?)%>", re.DOTALL)

# The first string literal inside the expression: the canonical 1992 value for
# getStarName, getConstellation, getColor, getPoint and swapIfSeeded alike.
_FIRST_LITERAL = re.compile(r'"([^"]*)"')

# Player-chosen, so there is no canonical value and a guess would be worse than
# a generic noun.
_PRONOUNS = {
    "getCaptainName": "the captain",
    "getShipName": "your ship",
}


def reduce_text(text: str) -> str:
    """Replace every interpolation with its canonical value."""

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")

        for name, word in _PRONOUNS.items():
            if name in body:
                return word

        # getPhrase splices in another phrase entirely; there is nothing
        # sensible to substitute, so drop it rather than invent.
        if "getPhrase" in body:
            return ""

        literal = _FIRST_LITERAL.search(body)
        return literal.group(1) if literal else ""

    reduced = _BLOCK.sub(replace, text)

    # Substitution can leave doubled spaces or a space before punctuation.
    reduced = re.sub(r"\s+", " ", reduced)
    return re.sub(r"\s+([,.;:!?])", r"\1", reduced).strip()


def has_interpolation(text: str) -> bool:
    return "<%" in text
