"""Claude backend, billed to the player's own Anthropic API account.

Uses the Claude Agent SDK with tools disabled and a single turn, so this is a
plain completion rather than an agent loop.

Authentication is ANTHROPIC_API_KEY by default, and no key is embedded - each
player supplies their own.

There is a second route, and it is deliberately not the default: with
allow_subscription the signed-in Claude CLI answers instead, on whatever plan
that account has. Anthropic's Agent SDK terms say that "unless previously
approved, Anthropic does not allow third party developers to offer claude.ai
login or rate limits for their products, including agents built on the Claude
Agent SDK". OFFER is the operative word. Using the subscription you already
pay for, on your own machine, is between you and your own account; shipping a
build that points other people at theirs is what the terms forbid. So the
setup menu offers it under your own name, marked personal use, and the
distributed default is a key.

Everything that is not specific to Anthropic - the prompts, the reply
contract, the retries - lives in conversation.py and is shared with every
other backend.
"""

from __future__ import annotations

import os

import anyio

from ..protocol import ConverseRequest, ConverseResponse, NarrateRequest
from .base import ProviderError
from .conversation import (  # noqa: F401 - re-exported; tests import these
    ConversationProvider,
    _MAX_JSON_ATTEMPTS,
    _body,
    _check,
    _correction,
    _looks_like_json,
    _names,
    _salvage,
    _schema_errors,
    _unstated_claims,
)

try:  # The SDK is optional; the mock must still work without it.
    from claude_agent_sdk import ClaudeAgentOptions, query

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on installs without it
    _SDK_AVAILABLE = False


class ClaudeProvider(ConversationProvider):
    """Single-turn completion through the Claude Agent SDK."""

    def __init__(self, model: str | None = None, timeout_s: float = 60.0,
                 api_key: str | None = None,
                 allow_subscription: bool = False) -> None:
        if not _SDK_AVAILABLE:
            raise ProviderError(
                "claude-agent-sdk is not installed; run: pip install claude-agent-sdk"
            )

        # An API key, not the signed-in CLI. Anthropic's Agent SDK terms are
        # explicit: "Unless previously approved, Anthropic does not allow
        # third party developers to offer claude.ai login or rate limits for
        # their products, including agents built on the Claude Agent SDK."
        # A mod that leaned on whoever happened to be signed in would be
        # doing exactly that, so the key is required and the player pays for
        # their own play through the API.
        #
        # The override exists for working on this locally against your own
        # account. It is personal use only - do not ship a build that sets
        # it, and do not tell players to.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        allow_subscription = allow_subscription or bool(
            os.environ.get("UQMAI_ALLOW_SUBSCRIPTION_AUTH")
        )
        if key:
            # The SDK reads the environment, so a key from the settings file
            # has to be put where it will be found.
            os.environ["ANTHROPIC_API_KEY"] = key
        elif not allow_subscription:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Conversation is billed to your "
                "own Anthropic API account: create a key at "
                "https://console.anthropic.com/settings/keys and set it, e.g. "
                "in PowerShell:\n\n"
                "    $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n\n"
                "A Claude Pro/Max subscription cannot be used here: Anthropic "
                "does not permit third-party products to authenticate with a "
                "claude.ai login. Play without AI with --no-ai."
            )

        self._model = model
        self._timeout_s = timeout_s
        self._last_result = ""

    @property
    def name(self) -> str:
        return "claude"

    @staticmethod
    def _describe(exc: Exception, last_result: str = "") -> str:
        """Turn an SDK failure into something a player can act on.

        The SDK wraps a failed run as "returned an error result: success",
        which says nothing at all. The CLI's own text is far more useful and
        is usually sitting in the last result message, so prefer it.
        """
        detail = (last_result or "").strip()
        lowered = detail.lower()

        if ("authenticate" in lowered or "login" in lowered
                or "expired" in lowered or "api key" in lowered):
            return (
                f"Anthropic rejected the credentials ({detail}). Check that "
                "ANTHROPIC_API_KEY is set to a valid key from "
                "https://console.anthropic.com/settings/keys and that the "
                "account has credit. Play without AI with --no-ai."
            )
        if detail:
            return f"the Claude CLI failed: {detail}"
        return f"claude call failed: {exc}"

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """The base contract is synchronous; the SDK is not."""
        return anyio.run(self._acomplete, system_prompt, user_prompt)

    async def _acomplete(self, system_prompt: str, user_prompt: str) -> str:
        options = ClaudeAgentOptions(
            allowed_tools=[],       # no agent loop; this is a plain completion
            system_prompt=system_prompt,
            max_turns=1,
        )
        if self._model:
            options.model = self._model

        result = ""
        with anyio.fail_after(self._timeout_s):
            async for message in query(prompt=user_prompt, options=options):
                # Take the final result only. Assistant blocks stream in too,
                # and collecting both duplicates the text.
                value = getattr(message, "result", None)
                if isinstance(value, str):
                    result = value
                    # The CLI reports failures such as an expired login in the
                    # result text and only then exits non-zero, so keep it: the
                    # exception that follows carries none of this detail.
                    self._last_result = value
        return result
