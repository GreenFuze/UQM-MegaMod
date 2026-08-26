"""The sidecar service: newline-delimited JSON over stdio.

One JSON object per line in, one per line out. The loop is deliberately dull:
every failure path answers with an error message and continues, because a
sidecar that dies or stalls turns an optional feature into a broken game.
"""

from __future__ import annotations

import sys
from typing import IO

from .pagination import paginate
from .voice import VoiceDirectory
from .persona import PromptBuilder
from .protocol import (
    MAX_SPOKEN_TEXT,
    PROTOCOL_VERSION,
    ConverseRequest,
    NarrateRequest,
    ProtocolError,
    ResponseValidator,
    decode_line,
    encode_line,
    to_display_text,
)
from .providers.base import LLMProvider, ProviderError, TTSProvider


class Sidecar:
    """Serves conversation turns for one character.

    Holds its streams for its lifetime and flushes after every message, since
    the game is blocking on a line-oriented read.
    """

    def __init__(
        self,
        builder: PromptBuilder,
        llm: LLMProvider,
        tts: TTSProvider | None = None,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
        log: IO[str] | None = None,
    ) -> None:
        self._builder = builder
        self._llm = llm
        self._tts = tts
        self._in = stdin if stdin is not None else sys.stdin
        self._out = stdout if stdout is not None else sys.stdout
        self._log = log if log is not None else sys.stderr
        self._voice = VoiceDirectory() if tts is not None else None

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

    def _speak(self, text: str) -> str | None:
        """Synthesise one line and return its bare filename, or None.

        Called only for text that will actually be spoken. Failure is never
        fatal: the game shows the subtitle over a carrier clip, which is
        exactly how it behaved before there was any synthesis at all.
        """
        if self._tts is None or self._voice is None:
            return None

        path, name = self._voice.next_file()
        try:
            self._tts.synthesise(text, self._builder.profile.key, str(path))
        except Exception as exc:  # noqa: BLE001 - a mute line beats a dead turn
            self._warn(f"synthesis failed, falling back to subtitles: {exc}")
            return None

        self._voice.prune()
        return name

    def _spoken_keys(self, refs: tuple[int, ...]) -> tuple[str, ...]:
        """The canonical phrases the character has actually spoken.

        Those are what he may draw on: he can repeat and elaborate on his own
        words, but cannot volunteer canon he has not reached. This is what
        keeps grounding and spoiler control on the same mechanism.
        """
        return tuple(
            key
            for key in (self._builder.key_for_ref(ref) for ref in refs)
            if key is not None
        )

    def _narrate(self, request: NarrateRequest) -> dict:
        """Reword an outcome the encounter has already produced and applied.

        Nothing is chosen here and nothing is validated against an action
        list, because there is no decision left: the handler ran, the state
        changed, and this call only decides how it sounds.
        """
        prompt = self._builder.render(
            permitted_keys=self._spoken_keys(request.spoken_refs),
            memory=(),
            visits=0,
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
            "audio_file": self._speak(spoken),
        }

    def _converse(self, request: ConverseRequest) -> dict:
        # The game identifies actions by numeric RESPONSE_REF, since enum
        # names do not exist at runtime in C. Resolve them so the persona and
        # the provider can reason about names.
        request = request.with_resolved_keys(self._builder.key_for_ref)

        permitted = request.available_knowledge or self._spoken_keys(
            request.spoken_refs
        )

        prompt = self._builder.render(
            permitted_keys=permitted,
            memory=request.memory,
            visits=request.visits,
        )

        # The provider is untrusted: whatever it returns is re-checked against
        # the actions this turn actually exported.
        raw = self._llm.generate(request, prompt)
        validator = ResponseValidator(request)
        response = validator.validate(raw)

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
            payload["audio_file"] = self._speak(response.spoken_text)

        return payload

    def _send(self, payload: dict) -> None:
        self._out.write(encode_line(payload) + "\n")
        self._out.flush()

    def _send_error(self, request_id: int, code: str, message: str) -> None:
        self._warn(f"request {request_id}: {code}: {message}")
        self._send(
            {"type": "error", "id": request_id, "code": code, "message": message}
        )

    def _warn(self, message: str) -> None:
        self._log.write(f"[uqm-ai] {message}\n")
        self._log.flush()
