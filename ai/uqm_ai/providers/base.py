"""Provider interfaces.

A provider's wire format never reaches game code. The internal contract is a
ConverseResponse, whatever the backend natively speaks - schema-constrained
JSON, native tool calls, or hand-written rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..protocol import ConverseRequest, ConverseResponse


class ProviderError(Exception):
    """Raised when a provider cannot produce a response.

    The sidecar converts this into a protocol error so the game can fall back;
    it is never allowed to propagate as a crash.
    """


class LLMProvider(ABC):
    """Turns a conversational request into a character's reply."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier reported in the handshake."""

    @abstractmethod
    def generate(self, request: ConverseRequest, system_prompt: str) -> ConverseResponse:
        """Produce a reply. Must not raise anything but ProviderError."""


class TTSProvider(ABC):
    """Synthesises speech for generated prose."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def synthesise(self, text: str, character: str, out_path: str) -> str:
        """Write a WAV to out_path and return the path actually written."""
