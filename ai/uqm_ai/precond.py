"""A tiny condition language for authored knowledge.

Every knowledge item a character can draw on carries a condition saying when
it becomes available. Those conditions are authored data, and authored data
must not be able to acquire authority it should not have - so this is a real
parser over a deliberately small grammar rather than eval() over Python.

    always | true               unconditional, from before the game begins
    FLAG                        shorthand for FLAG != 0
    FLAG >= 2                   ops: == != >= <= > <
    date >= 2157-01-01          the in-game calendar
    a and b, a or b, not a, (a) composition

Three properties eval() cannot give us:

  1. A flag the game did not send this turn reads as 0, matching
     getGameStateUint's own contract (lua/luastate.c:180-184: uninitialised
     properties are 0). Under eval() it would raise NameError and kill the turn.
  2. A malformed condition is fatal when the character file loads, not in the
     middle of a conversation.
  3. Nothing in a data file executes.

The grammar deliberately cannot express arithmetic, string comparison, or a
reference to anything but game state and the date. Whether a character may say
a thing is decided here; how it says it is the model's business.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol

# "always" reads better in a data file than "true"; both are accepted.
_TRUE_WORDS = frozenset({"always", "true"})

_COMPARE = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<lparen>\()"
    r"|(?P<rparen>\))"
    r"|(?P<op>==|!=|>=|<=|>|<)"
    r"|(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?P<int>\d+)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r")"
)


class PreconditionError(Exception):
    """Raised when a condition cannot be parsed."""


class Node(Protocol):
    def evaluate(self, state: Mapping[str, int], today: date) -> bool: ...
    def flags(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class Const:
    value: bool

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return self.value

    def flags(self) -> frozenset[str]:
        return frozenset()


@dataclass(frozen=True)
class Flag:
    """One named game-state flag compared against a literal."""

    name: str
    op: str
    value: int

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        # Absent means zero. The game says so, and a knowledge item guarded by
        # a flag the game never sends must stay shut rather than error.
        return _COMPARE[self.op](int(state.get(self.name, 0)), self.value)

    def flags(self) -> frozenset[str]:
        return frozenset({self.name})


@dataclass(frozen=True)
class Bit:
    """One bit of a flag the game uses as a bitmask.

    Several flags are registers rather than values. STARBASE_BULLETS is 32 bits,
    one per news item, set once the Commander has delivered it; KNOW_HOMEWORLD
    is 18; PKUNK_REASONS and ZOQFOT_KNOW_MASK are smaller ones.

    This exists because seven of the Commander's bulletins fire on
    CheckAlliance(), which reads the ship list rather than game state. The bit
    is the only evidence on the wire that the news ever happened.
    """

    name: str
    index: int

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return bool((int(state.get(self.name, 0)) >> self.index) & 1)

    def flags(self) -> frozenset[str]:
        return frozenset({self.name})


@dataclass(frozen=True)
class When:
    """A comparison against the in-game calendar."""

    op: str
    value: date

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return _COMPARE[self.op](today, self.value)

    def flags(self) -> frozenset[str]:
        return frozenset()


@dataclass(frozen=True)
class Not:
    term: Node

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return not self.term.evaluate(state, today)

    def flags(self) -> frozenset[str]:
        return self.term.flags()


@dataclass(frozen=True)
class All:
    terms: tuple[Node, ...]

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return all(t.evaluate(state, today) for t in self.terms)

    def flags(self) -> frozenset[str]:
        return frozenset().union(*(t.flags() for t in self.terms))


@dataclass(frozen=True)
class Any_:
    terms: tuple[Node, ...]

    def evaluate(self, state: Mapping[str, int], today: date) -> bool:
        return any(t.evaluate(state, today) for t in self.terms)

    def flags(self) -> frozenset[str]:
        return frozenset().union(*(t.flags() for t in self.terms))


class _Parser:
    """Recursive descent over the token list. Small enough to read in one go."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._tokens = self._tokenise(text)
        self._pos = 0

    def _tokenise(self, text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            if text[pos].isspace():
                pos += 1
                continue
            match = _TOKEN.match(text, pos)
            if match is None or match.end() == pos:
                raise PreconditionError(
                    f"cannot parse condition {text!r} at offset {pos}"
                )
            kind = match.lastgroup
            assert kind is not None
            tokens.append((kind, match.group(kind)))
            pos = match.end()
        if not tokens:
            raise PreconditionError("empty condition")
        return tokens

    # --- token helpers ---

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise PreconditionError(f"condition {self._text!r} ends unexpectedly")
        self._pos += 1
        return token

    def _at_word(self, word: str) -> bool:
        token = self._peek()
        return token is not None and token[0] == "name" and token[1] == word

    # --- grammar ---

    def parse(self) -> Node:
        node = self._expression()
        if self._peek() is not None:
            raise PreconditionError(
                f"trailing input in condition {self._text!r}: "
                f"{self._tokens[self._pos][1]!r}"
            )
        return node

    def _expression(self) -> Node:
        terms = [self._term()]
        joiner: str | None = None
        while self._at_word("and") or self._at_word("or"):
            word = self._take()[1]
            if joiner is not None and word != joiner:
                # Mixing without parentheses is ambiguous to a reader, and a
                # misread condition is a spoiler. Make the author be explicit.
                raise PreconditionError(
                    f"condition {self._text!r} mixes 'and' with 'or'; "
                    f"use parentheses"
                )
            joiner = word
            terms.append(self._term())

        if len(terms) == 1:
            return terms[0]
        return All(tuple(terms)) if joiner == "and" else Any_(tuple(terms))

    def _term(self) -> Node:
        if self._at_word("not"):
            self._take()
            return Not(self._term())
        return self._atom()

    def _atom(self) -> Node:
        kind, text = self._take()

        if kind == "lparen":
            node = self._expression()
            closing = self._take()
            if closing[0] != "rparen":
                raise PreconditionError(f"expected ')' in {self._text!r}")
            return node

        if kind != "name":
            raise PreconditionError(
                f"expected a flag name in {self._text!r}, got {text!r}"
            )

        if text in _TRUE_WORDS:
            return Const(True)

        if text == "date":
            op = self._take()
            if op[0] != "op":
                raise PreconditionError(
                    f"'date' needs a comparison in {self._text!r}"
                )
            value = self._take()
            if value[0] != "date":
                raise PreconditionError(
                    f"'date' needs a YYYY-MM-DD literal in {self._text!r}, "
                    f"got {value[1]!r}"
                )
            return When(op[1], date.fromisoformat(value[1]))

        following = self._peek()

        # NAME bit N - one bit of a bitmask flag.
        if (following is not None and following[0] == "name"
                and following[1] == "bit"):
            self._take()
            index = self._take()
            if index[0] != "int":
                raise PreconditionError(
                    f"'bit' needs a number in {self._text!r}, got {index[1]!r}"
                )
            position = int(index[1])
            if not 0 <= position <= 31:
                raise PreconditionError(
                    f"bit {position} is out of range in {self._text!r}; "
                    f"the widest flag the game has is 32 bits"
                )
            return Bit(text, position)

        # A bare flag name means "is set".
        if following is None or following[0] != "op":
            return Flag(text, "!=", 0)

        self._take()
        literal = self._take()
        if literal[0] != "int":
            raise PreconditionError(
                f"{text} must be compared with a number in {self._text!r}, "
                f"got {literal[1]!r}"
            )
        return Flag(text, following[1], int(literal[1]))


def parse(condition: str | list[str] | tuple[str, ...]) -> Node:
    """Parse one condition, or a list of them meaning 'all of these'."""
    if isinstance(condition, (list, tuple)):
        if not condition:
            raise PreconditionError("empty condition list")
        return All(tuple(parse(item) for item in condition))

    if not isinstance(condition, str):
        raise PreconditionError(f"condition must be a string, got {condition!r}")

    return _Parser(condition).parse()
