"""What guards each line a character speaks. Development tool, not shipped.

The game already encodes who knows what and when, in the if/else chains around
every NPCPhrase call. This reads those chains back out so a character file can
be written from the game rather than from memory - which matters, because the
base model knows Star Control II well enough to write something plausible and
wrong.

It is a heuristic C reader and not a parser, deliberately. It has to be right
enough to save an author from reading 2,109 lines of melnorm.c, and honest
about the rest: whatever it cannot resolve it reports as unguarded rather than
guessing. Roughly a fifth of NPCPhrase sites have a GET_GAME_STATE within
reach; the other four fifths are structural, and no amount of cleverness here
would change that.

Its output is evidence for a human, never generated data. Run it, read it,
write the character file yourself.

    python -m tools.draft_knowledge                    # every character
    python -m tools.draft_knowledge comm.starbase.dialogue
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uqm_ai.cast import Cast  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO.parent / "uqm-megamod-content"

_NPC_PHRASE = re.compile(r"NPCPhrase(?:_cb)?\s*\(\s*([A-Z_][A-Z0-9_]*)")
_GET_STATE = re.compile(r"GET_GAME_STATE\s*\(\s*([A-Z_][A-Z0-9_]*)\s*\)")
_SET_STATE = re.compile(r"SET_GAME_STATE\s*\(\s*([A-Z_][A-Z0-9_]*)")
_CONTROL = re.compile(r"\b(if|else\s+if|else|switch|while|for)\b")
_CASE = re.compile(r"^\s*case\s+([A-Za-z0-9_]+)\s*:")
_FUNC = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass
class Guard:
    """One enclosing condition, as written."""

    text: str
    flags: tuple[str, ...]


@dataclass
class Site:
    """One NPCPhrase call and everything guarding it."""

    phrase: str
    line: int
    function: str
    guards: list[Guard] = field(default_factory=list)

    @property
    def flags(self) -> list[str]:
        seen: list[str] = []
        for guard in self.guards:
            for flag in guard.flags:
                if flag not in seen:
                    seen.append(flag)
        return seen


def _strip(source: str) -> str:
    """Remove comments and string literals so braces inside them do not count."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", " ", source)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', source)


def scan(path: Path) -> list[Site]:
    """Every NPCPhrase site in one comm file, with its enclosing conditions.

    Tracks the brace stack, remembering the control-flow line that opened each
    block. A phrase is guarded by whichever of those lines tested game state.
    """
    lines = _strip(path.read_text(encoding="utf-8", errors="replace")).splitlines()

    sites: list[Site] = []
    stack: list[Guard | None] = []
    pending: str | None = None      # a control line whose brace has not opened yet
    function = "?"

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()

        # A function definition at column zero: name ( ... )
        if line[:1] not in (" ", "\t", "", "}") and _FUNC.match(line):
            function = _FUNC.match(line).group(1)

        for phrase in _NPC_PHRASE.findall(line):
            sites.append(Site(
                phrase=phrase,
                line=number,
                function=function,
                guards=[g for g in stack if g is not None],
            ))

        control = _CONTROL.search(stripped)
        if control and not stripped.startswith("}"):
            pending = stripped

        case = _CASE.match(line)
        if case and stack:
            # switch arms are not braced; attach the arm to the switch block.
            flags = tuple(_GET_STATE.findall(pending or ""))
            stack[-1] = Guard(f"case {case.group(1)}", flags)

        for char in line:
            if char == "{":
                if pending is not None:
                    flags = tuple(_GET_STATE.findall(pending))
                    stack.append(Guard(pending, flags) if flags else None)
                    pending = None
                else:
                    stack.append(None)
            elif char == "}":
                if stack:
                    stack.pop()

        if stripped.endswith(";"):
            pending = None

    return sites


def report(cast: Cast, resource: str) -> None:
    spec = cast.spec(resource)
    table = cast.table(resource)
    text = {e.key: (e.text or "") for e in table.entries}

    sources = sorted((REPO / "src" / "uqm" / "comm" / spec.source_dir).glob("*.c"))
    sites: list[Site] = []
    for source in sources:
        sites.extend(scan(source))

    guarded = [s for s in sites if s.flags]
    writes: set[str] = set()
    for source in sources:
        writes |= set(_SET_STATE.findall(_strip(
            source.read_text(encoding="utf-8", errors="replace"))))

    print(f"\n{'=' * 72}")
    print(f"{resource}   ({spec.source_dir} -> {spec.content_key})")
    print(f"{'=' * 72}")
    print(f"{len(sites)} NPCPhrase sites, {len(guarded)} guarded by game state")
    print(f"writes {len(writes)} flags\n")

    by_flags: dict[tuple[str, ...], list[Site]] = {}
    for site in guarded:
        by_flags.setdefault(tuple(site.flags), []).append(site)

    for flags, group in sorted(by_flags.items(), key=lambda kv: -len(kv[1])):
        print(f"  when {' and '.join(flags)}")
        for site in group:
            snippet = " ".join(text.get(site.phrase, "").split())[:64]
            print(f"      {site.phrase:<34} {site.function}:{site.line}")
            if snippet:
                print(f"          {snippet}")
        print()

    unguarded = sorted({s.phrase for s in sites if not s.flags})
    if unguarded:
        print(f"  structurally reachable, no state test ({len(unguarded)}):")
        print("      " + ", ".join(unguarded[:40]))
        if len(unguarded) > 40:
            print(f"      ... and {len(unguarded) - 40} more")


def main(argv: list[str]) -> int:
    cast = Cast(REPO, CONTENT)
    wanted = argv[1:] or sorted(cast.specs)

    for resource in wanted:
        if resource not in cast.specs:
            print(f"unknown character {resource!r}", file=sys.stderr)
            return 1
        report(cast, resource)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
