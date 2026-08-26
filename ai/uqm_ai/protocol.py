"""Wire protocol between the game and the AI sidecar.

Newline-delimited JSON over stdio, one object per line. See
docs/ai-architecture.md section 3.

The game performs the authoritative validation in C. The sidecar validates
too, so that a misbehaving model is caught where it misbehaves rather than
travelling across the wire first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1

# Bounds. A model that ignores them produces a rejected field, never an
# unbounded prompt or an unbounded save file.
MAX_SPOKEN_TEXT = 2000
MAX_REMEMBER = 400
# Long enough for a real argument. Note this REJECTS rather than
# truncates, so it must comfortably exceed what the game can send.
MAX_PLAYER_INPUT = 4000


class ProtocolError(Exception):
    """Raised when a message cannot be parsed or violates the contract."""


# Models write typographic punctuation by habit. UQM's bitmap fonts are 8-bit
# and have no glyph for these, so they reach the subtitle as replacement
# boxes. Folding them to the ASCII the game's own dialogue uses costs nothing.
_TYPOGRAPHIC = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": " - ", "―": "-", "−": "-",
    "…": "...", " ": " ", "•": "*", "·": "*",
    "‹": "<", "›": ">", "«": '"', "»": '"',
    "′": "'", "″": '"', "ʼ": "'",
}


def to_display_text(text: str) -> str:
    """Make generated prose safe for the game's subtitle renderer.

    Control characters would corrupt rendering; newlines survive because the
    game pages on them.
    """
    folded = "".join(_TYPOGRAPHIC.get(ch, ch) for ch in text)
    stripped = "".join(ch for ch in folded if ch >= " " or ch == "\n")

    # An em-dash becomes " - ", so one that was already spaced leaves a gap.
    # Collapsed per line, since newlines are the game's page breaks.
    return "\n".join(
        " ".join(part for part in line.split(" ") if part)
        for line in stripped.split("\n")
    )


# Where an action leads, relative to where the conversation is now. Mirrors
# AI_FLOW_* in aiconv.h; see that header for why this is measured against the
# current node rather than against ExitConversation.
FLOW_UNKNOWN = 0
FLOW_SAME_NODE = 1
FLOW_DEPARTS = 2


@dataclass(frozen=True)
class ActionSpec:
    """One action the encounter has exported this turn.

    Mirrors a RESPONSE_ENTRY in the game: the ref is the phrase enum value and
    the text is its canonical wording. `terminal` is always False on the wire
    (see aiconv.h); `flow` is the usable signal.
    """

    ref: int
    text: str
    terminal: bool
    flow: int = FLOW_UNKNOWN
    repeated: bool = False
    key: str | None = None  # resolved from the phrase table by the sidecar

    @property
    def stays_here(self) -> bool:
        """True when choosing this does not end or redirect the conversation.

        Note this says nothing about whether it makes progress: Fwiffo's
        join_us and his what_doing_on_pluto_1 are both wired back to the same
        handler, yet one is a dead end and the other is the gate. `repeated`
        is what tells them apart.
        """
        return self.flow == FLOW_SAME_NODE

    @property
    def changes_nothing(self) -> bool:
        """True when this was already chosen and the encounter kept offering it.

        Proof, not inference: an action the encounter consumes disappears
        from the next list. One that comes back was not consumed, so asking
        again yields the same kind of answer.
        """
        return self.repeated

    @property
    def is_consequential(self) -> bool:
        return self.terminal or self.flow == FLOW_DEPARTS

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ActionSpec:
        try:
            return cls(
                ref=int(raw["ref"]),
                text=str(raw["text"]),
                terminal=bool(raw.get("terminal", False)),
                flow=int(raw.get("flow", FLOW_UNKNOWN)),
                repeated=bool(raw.get("repeated", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError(f"action missing or invalid field: {exc}") from exc

    def resolved(self, key: str | None) -> ActionSpec:
        return ActionSpec(
            ref=self.ref,
            text=self.text,
            terminal=self.terminal,
            flow=self.flow,
            repeated=self.repeated,
            key=key,
        )


@dataclass(frozen=True)
class SessionRef:
    """Identifies which save and which character a turn belongs to.

    save_id is carried from the first protocol version even before memory is
    implemented, because retrofitting identity into a live protocol is far
    more expensive than reserving it.
    """

    save_id: str
    character: str
    encounter: str
    state_fingerprint: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SessionRef:
        return cls(
            save_id=str(raw.get("save_id", "")),
            character=str(raw.get("character", "")),
            encounter=str(raw.get("encounter", "")),
            state_fingerprint=str(raw.get("state_fingerprint", "")),
        )


def _session_of(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the session fields from either wire shape.

    The C writer emits a flat object - its JSON writer has no keyed
    nested-object support, and a smaller writer is worth more than a prettier
    wire format - while tests and the mock use the nested form.
    """
    session = raw.get("session")
    if session is not None:
        return session
    return {
        "save_id": raw.get("session_save_id", ""),
        "character": raw.get("session_character", ""),
        "encounter": raw.get("session_encounter", ""),
        "state_fingerprint": raw.get("session_state_fingerprint", ""),
    }


@dataclass(frozen=True)
class ConverseRequest:
    """One conversational turn, as sent by the game."""

    id: int
    session: SessionRef
    player_input: str
    actions: tuple[ActionSpec, ...]
    visits: int = 0
    available_knowledge: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    spoken_refs: tuple[int, ...] = ()

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ConverseRequest:
        if raw.get("type") != "converse":
            raise ProtocolError(f"expected type 'converse', got {raw.get('type')!r}")

        actions = tuple(ActionSpec.from_json(a) for a in raw.get("actions", ()))
        if not actions:
            raise ProtocolError("request exported no actions")

        player_input = str(raw.get("player_input", ""))
        if len(player_input) > MAX_PLAYER_INPUT:
            raise ProtocolError(f"player_input exceeds {MAX_PLAYER_INPUT} characters")

        context = raw.get("context", {})
        session = _session_of(raw)
        visits = int(raw.get("visits", context.get("visits", 0)))
        knowledge = raw.get("available_knowledge", context.get("available_knowledge", ()))
        memory = raw.get("memory", context.get("memory", ()))
        spoken = tuple(int(r) for r in raw.get("spoken_refs", ()) if isinstance(r, int))
        try:
            request_id = int(raw["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("request missing valid integer id") from exc

        return cls(
            id=request_id,
            session=SessionRef.from_json(session),
            player_input=player_input,
            actions=actions,
            visits=visits,
            available_knowledge=tuple(knowledge),
            memory=tuple(memory),
            spoken_refs=spoken,
        )

    def action_refs(self) -> frozenset[int]:
        return frozenset(a.ref for a in self.actions)

    def by_key(self, key: str) -> ActionSpec | None:
        for action in self.actions:
            if action.key == key:
                return action
        return None

    def with_resolved_keys(self, resolve) -> ConverseRequest:
        """Return a copy whose actions carry their phrase-table key."""
        return ConverseRequest(
            id=self.id,
            session=self.session,
            player_input=self.player_input,
            actions=tuple(a.resolved(resolve(a.ref)) for a in self.actions),
            visits=self.visits,
            available_knowledge=self.available_knowledge,
            memory=self.memory,
            spoken_refs=self.spoken_refs,
        )


@dataclass(frozen=True)
class NarrateRequest:
    """A turn whose action has already been dispatched.

    The encounter has run its handler and produced authored_text: that IS the
    outcome, and it is not negotiable. All that is wanted back is the same
    thing said in the character's voice.

    This exists because generating prose before the dispatch let the model
    agree to things the handler then refused - the player was promised
    something and nothing happened.
    """

    id: int
    session: SessionRef
    player_input: str
    authored_text: str
    spoken_refs: tuple[int, ...] = ()

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> NarrateRequest:
        if raw.get("type") != "narrate":
            raise ProtocolError(f"expected type 'narrate', got {raw.get('type')!r}")

        authored = str(raw.get("authored_text", "")).strip()
        if not authored:
            raise ProtocolError("narrate request carried no authored_text")

        player_input = str(raw.get("player_input", ""))
        if len(player_input) > MAX_PLAYER_INPUT:
            raise ProtocolError(f"player_input exceeds {MAX_PLAYER_INPUT} characters")

        try:
            request_id = int(raw["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("request missing valid integer id") from exc

        return cls(
            id=request_id,
            session=SessionRef.from_json(_session_of(raw)),
            player_input=player_input,
            authored_text=authored,
            spoken_refs=tuple(
                int(r) for r in raw.get("spoken_refs", ()) if isinstance(r, int)
            ),
        )


@dataclass(frozen=True)
class ConverseResponse:
    """The sidecar's reply for one turn."""

    id: int
    spoken_text: str
    action: int | None = None
    remember: str | None = None
    audio_path: str | None = None

    def to_json(self) -> dict[str, Any]:
        audio = {"format": "wav", "path": self.audio_path} if self.audio_path else None
        return {
            "type": "converse",
            "id": self.id,
            "spoken_text": self.spoken_text,
            "action": self.action,
            "remember": self.remember,
            "audio": audio,
        }


class ResponseValidator:
    """Enforces the response contract against the request that produced it.

    Rejecting a field degrades that field only: an unusable action becomes
    None and the prose survives, because losing a state transition is always
    safer than acting on one the encounter never offered.
    """

    def __init__(self, request: ConverseRequest) -> None:
        self._request = request
        self._permitted = request.action_refs()
        self._rejections: list[str] = []

    @property
    def rejections(self) -> tuple[str, ...]:
        """Notes on what was rejected, for the developer log."""
        return tuple(self._rejections)

    def validate(self, response: ConverseResponse) -> ConverseResponse:
        action = self._validate_action(response.action)
        spoken = self._validate_spoken(response.spoken_text)
        remember = self._validate_remember(response.remember)

        return ConverseResponse(
            id=self._request.id,
            spoken_text=spoken,
            action=action,
            remember=remember,
            audio_path=response.audio_path,
        )

    def _validate_action(self, action: int | None) -> int | None:
        if action is None:
            return None
        if action not in self._permitted:
            self._rejections.append(
                f"action {action!r} was not exported this turn; permitted: "
                f"{sorted(self._permitted)}"
            )
            return None
        return action

    def _validate_spoken(self, spoken_text: str) -> str:
        spoken = to_display_text((spoken_text or "").strip())

        if len(spoken) > MAX_SPOKEN_TEXT:
            self._rejections.append(f"spoken_text truncated to {MAX_SPOKEN_TEXT}")
            spoken = spoken[:MAX_SPOKEN_TEXT]
        if not spoken:
            raise ProtocolError("response has empty spoken_text")
        return spoken

    def _validate_remember(self, remember: str | None) -> str | None:
        if remember is None:
            return None
        # Folded like spoken text: this is quoted back into later prompts and
        # is destined for the save, so it should not carry punctuation the
        # game cannot render either.
        text = to_display_text(remember.strip())
        if not text:
            return None
        if len(text) > MAX_REMEMBER:
            self._rejections.append(f"remember truncated to {MAX_REMEMBER}")
            text = text[:MAX_REMEMBER]
        return text


def encode_line(payload: dict[str, Any]) -> str:
    """Serialise one message as a single NDJSON line."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_line(line: str) -> dict[str, Any]:
    """Parse one NDJSON line, failing with context rather than a bare error."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"expected a JSON object, got {type(payload).__name__}")
    return payload
