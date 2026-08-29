"""The sidecar service: newline-delimited JSON over stdio.

One JSON object per line in, one per line out. The loop is deliberately dull:
every failure path answers with an error message and continues, because a
sidecar that dies or stalls turns an optional feature into a broken game.
"""

from __future__ import annotations

import sys
from typing import IO

from . import gamelog
from .cast import Cast, CastError
from .memory import MemoryStore
from .recap import Encounter, Recapper
from .pagination import paginate
from .persona import PromptBuilder
from .voice import VoiceDirectory
from .protocol import (
    MAX_SPOKEN_TEXT,
    GAME_START,
    PROTOCOL_VERSION,
    ConverseRequest,
    EncounterEnd,
    NarrateRequest,
    ProtocolError,
    ResponseValidator,
    decode_line,
    encode_line,
    to_display_text,
)
from .providers.base import LLMProvider, ProviderError, TTSProvider


class Sidecar:
    """Serves conversation turns for every character the cast can voice.

    One process, not one per character: the speech model costs 23 seconds to
    load and is shared, and the game keeps the sidecar alive across encounters
    anyway. The character is chosen per request from session.character, which
    the game has always sent and nothing used to read.

    Holds its streams for its lifetime and flushes after every message, since
    the game is blocking on a line-oriented read.
    """

    def __init__(
        self,
        cast: Cast,
        llm: LLMProvider,
        tts: TTSProvider | None = None,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
        log: IO[str] | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self._cast = cast
        self._llm = llm
        self._tts = tts
        self._in = stdin if stdin is not None else sys.stdin
        self._out = stdout if stdout is not None else sys.stdout
        self._log = log if log is not None else sys.stderr
        self._voice = VoiceDirectory() if tts is not None else None
        self._memory = memory if memory is not None else MemoryStore()
        self._recap = Recapper(llm, self._memory, self._warn)
        # What has been said in the conversation now in progress, if any.
        self._encounter: Encounter | None = None
        self._last_date = GAME_START

    def run(self) -> None:
        """Serve until stdin closes."""
        for line in self._in:
            line = line.strip()
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        # Parse far enough to recover the request id, so that an error can be
        # correlated by the game even when the rest of the message is junk.
        request_id = 0
        try:
            payload = decode_line(line)
            request_id = int(payload.get("id", 0) or 0)

            kind = payload.get("type")
            if kind == "hello":
                self._send(self._hello_reply())
                return
            if kind == "encounter_end":
                # A notification, not a request. The game does not wait, so
                # there is nothing to reply to.
                self._end_encounter(EncounterEnd.from_json(payload))
                return
            if kind == "narrate":
                self._send(self._narrate(NarrateRequest.from_json(payload)))
                return
            if kind != "converse":
                raise ProtocolError(f"unknown message type {kind!r}")

            self._send(self._converse(ConverseRequest.from_json(payload)))

        except ProtocolError as exc:
            self._send_error(request_id, "protocol_error", str(exc))
        except ProviderError as exc:
            self._send_error(request_id, "provider_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - last line of defence
            # An unexpected fault must still leave the game able to fall back.
            self._send_error(request_id, "internal_error", repr(exc))

    def _hello_reply(self) -> dict:
        return {
            "type": "ready",
            "protocol": PROTOCOL_VERSION,
            "llm": True,
            "tts": self._tts is not None,
            "provider": self._llm.name,
            # Native path, for the game to mount. Empty when there is no
            # synthesis, in which case the game keeps using a carrier clip.
            "voice_dir": self._voice.native_path if self._voice else "",
        }

    def _builder_for(self, character: str) -> PromptBuilder:
        """The prompt builder for the character this request is addressed to.

        An unknown or unauthored character is a protocol error, not a crash:
        the game logs it and falls back to that race's own dialogue menu, which
        is how a partially authored cast ships safely.
        """
        try:
            return self._cast.builder(character)
        except CastError as exc:
            raise ProtocolError(str(exc)) from exc

    def _speak(self, text: str, character: str) -> str | None:
        """Synthesise one line and return its bare filename, or None.

        Called only for text that will actually be spoken. Failure is never
        fatal: the game shows the subtitle over a carrier clip, which is
        exactly how it behaved before there was any synthesis at all.
        """
        if self._tts is None or self._voice is None:
            return None

        path, name = self._voice.next_file()
        try:
            self._tts.synthesise(text, character, str(path))
        except Exception as exc:  # noqa: BLE001 - a mute line beats a dead turn
            self._warn(f"synthesis failed, falling back to subtitles: {exc}")
            return None

        self._voice.prune()
        return name

    @staticmethod
    def _spoken_keys(
        builder: PromptBuilder, refs: tuple[int, ...]
    ) -> tuple[str, ...]:
        """The canonical phrases the character has actually spoken.

        Those are what he may draw on: he can repeat and elaborate on his own
        words, but cannot volunteer canon he has not reached. This is what
        keeps grounding and spoiler control on the same mechanism.
        """
        return tuple(
            key
            for key in (builder.key_for_ref(ref) for ref in refs)
            if key is not None
        )

    def _current(self, save_id: str, character: str) -> Encounter:
        """The conversation in progress, starting one if the character changed.

        The game sends encounter_end, but a crash or a hard quit will not, so
        a change of character also closes the previous meeting rather than
        letting two conversations blur into one recollection.
        """
        current = self._encounter
        if current is not None and current.character != character:
            self._recap.finish(current, self._last_date)
            current = None
        if current is None:
            current = Encounter(save_id=save_id, character=character)
            self._encounter = current
        return current

    def _end_encounter(self, notice: EncounterEnd) -> None:
        current = self._encounter
        self._encounter = None
        if current is None or current.character != notice.character:
            return
        self._recap.finish(current, notice.game_date)

    def _narrate(self, request: NarrateRequest) -> dict:
        """Reword an outcome the encounter has already produced and applied.

        Nothing is chosen here and nothing is validated against an action
        list, because there is no decision left: the handler ran, the state
        changed, and this call only decides how it sounds.
        """
        builder = self._builder_for(request.session.character)
        prompt = builder.render(
            permitted_keys=self._spoken_keys(builder, request.spoken_refs),
            memory=(),
            visits=0,
            state=request.state,
            today=request.game_date,
        )

        spoken = to_display_text(self._llm.narrate(request, prompt).strip())
        if not spoken:
            raise ProtocolError("narrate produced no text")
        if len(spoken) > MAX_SPOKEN_TEXT:
            self._warn(f"request {request.id}: narration truncated")
            spoken = spoken[:MAX_SPOKEN_TEXT]

        return {
            "type": "narrate",
            "id": request.id,
            "spoken_text": paginate(spoken),
            "audio_file": self._speak(spoken, request.session.character),
        }

    def _converse(self, request: ConverseRequest) -> dict:
        # The game identifies actions by numeric RESPONSE_REF, since enum
        # names do not exist at runtime in C. Resolve them so the persona and
        # the provider can reason about names.
        self._last_date = request.game_date
        builder = self._builder_for(request.session.character)
        request = request.with_resolved_keys(builder.key_for_ref)

        # The permitted set is a union, not a choice. The floor is what the
        # character has already said this conversation; on top of that go the
        # canonical phrases this point in the story has unlocked. A character
        # with no knowledge entries therefore behaves exactly as Fwiffo does.
        spoken = self._spoken_keys(builder, request.spoken_refs)
        unlocked = builder.unlocked_keys(request.state, request.game_date)
        permitted = tuple(
            dict.fromkeys(request.available_knowledge + spoken + unlocked)
        )

        recalled = self._memory.recall(
            request.session.save_id, request.session.character,
            request.game_date,
        )
        prompt = builder.render(
            permitted_keys=permitted,
            memory=request.memory or recalled,
            visits=request.visits,
            state=request.state,
            today=request.game_date,
        )
        gamelog.emit(
            f"{request.session.character}: {len(request.state)} flags, "
            f"{len(permitted)} phrases permitted, "
            f"{len(builder.profile.active_denials(request.state, request.game_date))} "
            f"denials, on {request.game_date.isoformat()}"
        )

        # The provider is untrusted: whatever it returns is re-checked against
        # the actions this turn actually exported.
        raw = self._llm.generate(request, prompt)
        validator = ResponseValidator(request)
        response = validator.validate(raw)

        # The turn is kept so the whole meeting can be summarised when it
        # ends. `remember` is what the character thought mattered, and goes in
        # as a hint rather than as a memory of its own - a note written before
        # anyone knew where the conversation was going is a fragment, not a
        # recollection.
        encounter = self._current(request.session.save_id,
                request.session.character)
        encounter.add(request.player_input, response.spoken_text)
        if response.remember:
            encounter.note(response.remember)

        for note in validator.rejections:
            self._warn(f"request {request.id}: {note}")

        # Break the prose into subtitle pages. The game pages on newlines and
        # times each page itself; one long line overflows the subtitle box.
        payload = response.to_json()
        payload["spoken_text"] = paginate(payload["spoken_text"])

        # Only synthesise what the player will hear. When an action fires the
        # game discards this prose and asks for a narration of the outcome
        # instead, so voicing it here would pay for audio nobody plays.
        if response.action is None:
            payload["audio_file"] = self._speak(
                response.spoken_text, request.session.character
            )

        return payload

    def _send(self, payload: dict) -> None:
        # Shared with the diagnostic channel: the voice model warms up on its
        # own thread, and a log line landing inside a reply would corrupt it.
        with gamelog.writer_lock():
            self._out.write(encode_line(payload) + "\n")
            self._out.flush()

    def _send_error(self, request_id: int, code: str, message: str) -> None:
        self._warn(f"request {request_id}: {code}: {message}")
        self._send(
            {"type": "error", "id": request_id, "code": code, "message": message}
        )

    def _warn(self, message: str) -> None:
        gamelog.emit(message)
