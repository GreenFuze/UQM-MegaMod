"""The sidecar service: newline-delimited JSON over stdio.

One JSON object per line in, one per line out. The loop is deliberately dull:
every failure path answers with an error message and continues, because a
sidecar that dies or stalls turns an optional feature into a broken game.
"""

from __future__ import annotations

import sys
from typing import IO

from .persona import PromptBuilder
from .protocol import (
    PROTOCOL_VERSION,
    ConverseRequest,
    ProtocolError,
    ResponseValidator,
    decode_line,
    encode_line,
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
        }

    def _converse(self, request: ConverseRequest) -> dict:
        prompt = self._builder.render(
            permitted_keys=request.available_knowledge,
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

        return response.to_json()

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
