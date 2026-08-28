"""The set of game-state flags the game actually has.

Authored conditions name flags as strings, and a name the game does not know
reads as 0 forever - so a typo does not raise, it silently produces a piece of
knowledge the character can never unlock. That failure is invisible in play
and indistinguishable from "we have not reached that part of the story yet",
which is why it is worth a load-time check.

save.c is the authority rather than globdata.h: globdata.h:233-236 says its own
enum "is now only used for the symbolic names, and the comments", while
save.c's table defines the serialised layout and is what getGameStateUint is
keyed on.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# { "SHOFIXTI_VISITS", 3 },
_ENTRY = re.compile(r'\{\s*"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"\s*,\s*(?P<bits>\d+)\s*\}')


class GameStateError(Exception):
    """Raised when the flag table cannot be read."""


@lru_cache(maxsize=4)
def flag_bits(repo: Path) -> dict[str, int]:
    """Every named state flag, mapped to its width in bits."""
    path = Path(repo) / "src" / "uqm" / "save.c"
    if not path.is_file():
        raise GameStateError(f"game state table not found: {path}")

    source = path.read_text(encoding="utf-8", errors="replace")
    start = source.find("gameStateBitMap[]")
    if start < 0:
        raise GameStateError(f"no gameStateBitMap in {path}")
    end = source.find("};", start)

    flags = {m.group("name"): int(m.group("bits"))
             for m in _ENTRY.finditer(source, start, end)}
    if not flags:
        raise GameStateError(f"gameStateBitMap in {path} is empty")
    return flags


# Values the game computes rather than stores, sent alongside the real flags.
#
# Several characters gate what they say on the state of the ship and fleet
# rather than on a saved flag - starbas.c:802-910 is the clearest case, testing
# fleet strength, ally count and resource units to decide what advice the
# Commander gives. None of that lives in gameStateBitMap, so a knowledge model
# built only on saved flags could not reproduce his own briefing.
#
# They are namespaced SIS_ (the game's own name for the flagship, from
# GLOBAL_SIS) so they cannot collide with a real flag, and aistate.c computes
# them with the same public calls the conversation code uses.
DERIVED_VALUES = {
    "SIS_ALLY_COUNT": "allied non-human races, by CheckAlliance",
    "SIS_FLEET_STRENGTH": "CalculateEscortsWorth ()",
    "SIS_RESOURCE_UNITS": "GLOBAL_SIS (ResUnits)",
    "SIS_FUEL": "GLOBAL_SIS (FuelOnBoard)",
    "SIS_CREW": "GLOBAL_SIS (CrewEnlisted)",
    "SIS_LANDERS": "GLOBAL_SIS (NumLanders)",
}


def flag_names(repo: Path) -> frozenset[str]:
    """Every name a condition may reference: saved flags plus derived values."""
    return frozenset(flag_bits(repo)) | frozenset(DERIVED_VALUES)


def max_value(repo: Path, name: str) -> int:
    """The largest value a flag can hold, from its declared bit width."""
    bits = flag_bits(repo).get(name)
    if bits is None:
        raise GameStateError(f"unknown game state flag {name!r}")
    return (1 << bits) - 1
