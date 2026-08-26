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


@dataclass(frozen=True)
class ActionSpec:
    """One action the encounter has exported this turn.

    Mirrors a RESPONSE_ENTRY in the game: the id is the phrase enum key, the
    text is its canonical wording, and terminal is true when the handler is
    ExitConversation.
    """

    ref: int
    text: str
    terminal: bool
    key: str | None = None  # resolved from the phrase table by the sidecar

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ActionSpec:
        try:
            return cls(
                ref=int(raw["ref"]),
                text=str(raw["text"]),
                terminal=bool(raw.get("terminal", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError(f"action missing or invalid field: {exc}") from exc

    def resolved(self, key: str | None) -> ActionSpec:
        return ActionSpec(ref=self.ref, text=self.text, terminal=self.terminal, key=key)


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

        # The C writer emits a flat object (its JSON writer has no keyed
        # nested-object support, and a smaller writer is worth more than a
        # prettier wire format). Accept both shapes.
        context = raw.get("context", {})
        session = raw.get("session")
        if session is None:
            session = {
                "save_id": raw.get("session_save_id", ""),
                "character": raw.get("session_character", ""),
                "encounter": raw.get("session_encounter", ""),
                "state_fingerprint": raw.get("session_state_fingerprint", ""),
            }
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
        spoken = (spoken_text or "").strip()

        # Strip control characters, which would corrupt subtitle rendering.
        spoken = "".join(ch for ch in spoken if ch >= " " or ch == "\n")

        if len(spoken) > MAX_SPOKEN_TEXT:
            self._rejections.append(f"spoken_text truncated to {MAX_SPOKEN_TEXT}")
            spoken = spoken[:MAX_SPOKEN_TEXT]
        if not spoken:
            raise ProtocolError("response has empty spoken_text")
        return spoken

    def _validate_remember(self, remember: str | None) -> str | None:
        if remember is None:
            return None
        text = remember.strip()
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
