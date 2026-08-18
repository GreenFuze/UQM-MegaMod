"""Claude provider, backed by the player's own subscription.

Uses the Claude Agent SDK with tools disabled and a single turn, so this is a
plain completion rather than an agent loop. Authentication is whatever the
Claude Code CLI already holds, which means the player's own subscription pays
for their own play and no key is embedded anywhere.

The model is asked for a small JSON object. Its output is never trusted:
ResponseValidator re-checks the chosen action against what the encounter
actually exported, and malformed output degrades to conversation rather than
failing the turn.
"""

from __future__ import annotations

import json
import re

import anyio

from ..protocol import ConverseRequest, ConverseResponse
from .base import LLMProvider, ProviderError

try:  # The SDK is optional; the mock must still work without it.
    from claude_agent_sdk import ClaudeAgentOptions, query

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on installs without it
    _SDK_AVAILABLE = False


_RESPONSE_FORMAT = """
Reply with ONLY a JSON object, no prose around it, no code fence. Fill the
fields IN THIS ORDER - decide before you write, or you will drift into
speaking and forget to act:

{"considering": "<one short line: does this exchange match a listed ref? which one, and are you willing?>",
 "action": <the ref number, or null>,
 "spoken_text": "<what you say, first person, in character>",
 "remember": "<one short line worth recalling next time, or null>"}
""".strip()


class ClaudeProvider(LLMProvider):
    """Single-turn completion through the Claude Agent SDK."""

    def __init__(self, model: str | None = None, timeout_s: float = 60.0) -> None:
        if not _SDK_AVAILABLE:
            raise ProviderError(
                "claude-agent-sdk is not installed; run: pip install claude-agent-sdk"
            )
        self._model = model
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "claude"

    def generate(self, request: ConverseRequest, system_prompt: str) -> ConverseResponse:
        user_prompt = self._build_user_prompt(request)

        try:
            raw = anyio.run(self._complete, system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced to the game as an error
            raise ProviderError(f"claude call failed: {exc}") from exc

        return self._parse(raw, request)

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
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
        return result

    @staticmethod
    def _build_user_prompt(request: ConverseRequest) -> str:
        lines: list[str] = []

        if request.memory:
            lines.append("You remember from earlier meetings:")
            lines.extend(f"- {item}" for item in request.memory)
            lines.append("")

        lines.append("The captain says to you:")
        lines.append(f'"{request.player_input}"')
        lines.append("")

        lines.append(
            "The captain's words may correspond to one of the lines below. "
            "These are things THE CAPTAIN might be saying to you - they are "
            "NOT your own lines, and you must never speak them yourself."
        )
        for action in request.actions:
            ending = " [would end the conversation]" if action.terminal else ""
            lines.append(f'  {action.ref} = the captain means: "{action.text}"{ending}')
        lines.append("")
        lines.append(
            "Set action to the ref that best represents what has just happened "
            "in this exchange - either because the captain effectively said "
            "that line, or because you have DECIDED to go along with it. If "
            "you agree to something, you must set the matching ref; saying yes "
            "in words alone changes nothing and the moment is lost."
        )
        lines.append(
            "Use null when nothing on the list reflects the exchange, or when "
            "you are refusing. Refusing is a real choice - say so in character."
        )
        lines.append(
            "Say only what you actually know. Do not invent Spathi history, "
            "names, places or events that were not given to you above. If you "
            "do not know something, say so, change the subject, or lie in a "
            "way that is obviously Fwiffo being evasive."
        )
        lines.append("")
        lines.append(_RESPONSE_FORMAT)

        return "\n".join(lines)

    @staticmethod
    def _parse(raw: str, request: ConverseRequest) -> ConverseResponse:
        text = (raw or "").strip()
        if not text:
            raise ProviderError("claude returned no text")

        # Strip a code fence if the model added one despite instructions.
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()

        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except (json.JSONDecodeError, ValueError):
            # Malformed output degrades to pure conversation rather than
            # failing the turn: the player still gets a reply, and no state
            # transition happens, which is the safe direction.
            return ConverseResponse(
                id=request.id, spoken_text=text, action=None, remember=None
            )

        action = payload.get("action")
        if isinstance(action, str) and action.isdigit():
            action = int(action)
        if not isinstance(action, int):
            action = None

        remember = payload.get("remember")
        if not isinstance(remember, str) or not remember.strip():
            remember = None

        return ConverseResponse(
            id=request.id,
            spoken_text=str(payload.get("spoken_text", "")).strip(),
            action=action,
            remember=remember,
        )
