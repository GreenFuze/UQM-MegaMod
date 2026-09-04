"""Any backend that speaks the OpenAI chat-completions protocol.

One class covers OpenAI itself, Ollama, llama.cpp's server, LM Studio,
vLLM and OpenRouter, because they all accept the same request shape at
/chat/completions. Which one you get is a base URL and a model name.

A local model is the reason this exists. Both Anthropic and OpenAI bill their
APIs separately from their chat subscriptions, and neither permits a
third-party program to authenticate as a subscriber - so without this, playing
costs money per conversation. Pointed at Ollama it costs nothing, needs no
account, and never leaves the machine.

Written on urllib rather than the openai package deliberately: one completion
per turn does not justify a dependency, and a mod people install to play a
1992 game should ask for as little as possible.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import ProviderError
from .conversation import ConversationProvider

# Where each preset points. "local" is Ollama's default port; it serves an
# OpenAI-compatible API at /v1 and needs no key.
PRESETS = {
    "openai": ("https://api.openai.com/v1", "gpt-4o", "OPENAI_API_KEY"),
    "local": ("http://localhost:11434/v1", "llama3.1:8b", None),
}


class OpenAICompatProvider(ConversationProvider):
    """Chat completions over HTTP, against any compatible server."""

    def __init__(
        self,
        preset: str = "openai",
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        if preset not in PRESETS:
            raise ProviderError(
                f"unknown preset {preset!r}; expected one of "
                + ", ".join(sorted(PRESETS))
            )
        default_url, default_model, key_var = PRESETS[preset]

        # Environment overrides the preset, arguments override both, so one
        # provider serves "OpenAI proper" and "whatever I am running on this
        # machine" without a second code path.
        self._base = (
            base_url or os.environ.get("UQMAI_BASE_URL") or default_url
        ).rstrip("/")
        self._model = model or os.environ.get("UQMAI_MODEL") or default_model
        self._preset = preset
        self._timeout_s = timeout_s
        self._last_result = ""

        self._key = api_key or None
        if self._key is None and key_var is not None:
            self._key = os.environ.get(key_var)
        if self._key is None:
            self._key = os.environ.get("UQMAI_API_KEY")

        # A hosted endpoint without a key fails on the first turn, mid
        # conversation, which reads as the game hanging. Say so at startup.
        if key_var is not None and not self._key:
            raise ProviderError(
                f"{key_var} is not set. Conversation through {self._base} is "
                f"billed to your own account: create a key and set it, e.g. "
                f"in PowerShell:\n\n"
                f"    $env:{key_var} = '...'\n\n"
                "To play at no cost, run a local model instead "
                "(--provider local), or play without AI using --no-ai."
            )

    @property
    def name(self) -> str:
        return f"{self._preset}:{self._model}"

    @staticmethod
    def _describe(exc: Exception, last_result: str = "") -> str:
        detail = (last_result or "").strip()

        if isinstance(exc, urllib.error.HTTPError):
            code = exc.code
            if code in (401, 403):
                return (
                    f"the server rejected the credentials ({code}). Check the "
                    f"API key for this endpoint. {detail}"
                )
            if code == 404:
                return (
                    f"no such model or endpoint ({code}). Check UQMAI_MODEL "
                    f"and UQMAI_BASE_URL. {detail}"
                )
            if code == 429:
                return f"rate limited or out of credit ({code}). {detail}"
            return f"the server returned HTTP {code}. {detail}"

        if isinstance(exc, urllib.error.URLError):
            return (
                f"could not reach the model server ({exc.reason}). If you are "
                "running a local model, check it is started and listening - "
                "for Ollama that is 'ollama serve'. Play without AI with "
                "--no-ai."
            )

        return f"the model call failed: {exc}. {detail}".strip()

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # No streaming: one whole reply is wanted, and the game is
            # blocked on it either way.
            "stream": False,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"

        request = urllib.request.Request(
            f"{self._base}/chat/completions", data=body, headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The body carries the real reason - an unknown model, an expired
            # key - and the status alone does not.
            try:
                self._last_result = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001 - diagnosis only
                self._last_result = ""
            raise

        choices = payload.get("choices") or []
        if not choices:
            self._last_result = json.dumps(payload)[:400]
            raise ProviderError("the server returned no choices")

        text = (choices[0].get("message") or {}).get("content") or ""
        self._last_result = text[:400]
        return text
