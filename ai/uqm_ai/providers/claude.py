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
Reply with ONLY a JSON object, no prose around it, no code fence:

{"matches_ref": <the ref this exchange corresponds to, or null>,
 "willing": <true if you are going along with it, false if you refuse>,
 "spoken_text": "<what you say, first person, in character>",
 "remember": "<one short line worth recalling next time, or null>"}

matches_ref is about MEANING, not agreement: if the captain is inviting you
along, that is the invitation ref whether or not you accept. willing is your
decision about it. Answer both honestly - if you say yes in spoken_text but
set willing to false, nothing happens and the moment is lost.
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
        self._last_result = ""

    @property
    def name(self) -> str:
        return "claude"

    def generate(self, request: ConverseRequest, system_prompt: str) -> ConverseResponse:
        user_prompt = self._build_user_prompt(request)

        try:
            raw = anyio.run(self._complete, system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced to the game as an error
            raise ProviderError(self._describe(exc, self._last_result)) from exc

        return self._parse(raw, request)


    @staticmethod
    def _describe(exc: Exception, last_result: str = "") -> str:
        """Turn an SDK failure into something a player can act on.

        The SDK wraps a failed run as "returned an error result: success",
        which says nothing at all. The CLI's own text is far more useful and
        is usually sitting in the last result message, so prefer it.
        """
        detail = (last_result or "").strip()
        lowered = detail.lower()

        if "authenticate" in lowered or "login" in lowered or "expired" in lowered:
            return (
                f"the Claude CLI is not signed in ({detail}). Run 'claude' in a "
                "terminal, type /login, and complete sign-in in the browser. "
                "Starting the CLI alone does not re-authenticate an expired "
                "session."
            )
        if detail:
            return f"the Claude CLI failed: {detail}"
        return f"claude call failed: {exc}"

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
                    # The CLI reports failures such as an expired login in the
                    # result text and only then exits non-zero, so keep it: the
                    # exception that follows carries none of this detail.
                    self._last_result = value
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
            "Decide two things: which of those lines this exchange corresponds "
            "to in meaning, and whether you are going along with it."
        )
        lines.append(
            "When no listed line matches what the captain SAID, match on TONE "
            "instead: friendly, demanding, apologetic, threatening, curious. "
            "Early in a conversation the listed lines are simply ways of "
            "opening it, so the captain's manner is what actually "
            "distinguishes them, and picking the closest in tone is right "
            "even when the words differ completely."
        )
        lines.append(
            "IMPORTANT - the conversation only moves forward when you pick a "
            "ref. If you keep choosing null, the captain is stuck repeating "
            "themselves and nothing further ever becomes possible. For the "
            "ordinary back-and-forth lines (those not marked as ending the "
            "conversation), lean towards picking the closest reasonable "
            "match so things progress. Precision matters far more for the "
            "lines that end the conversation - only pick one of those if the "
            "captain clearly means it."
        )
        lines.append(
            "If the captain asks for something that is NOT on the list - to "
            "join them when no invitation is listed, for instance - you must "
            "NOT simply agree, because nothing would happen and the moment "
            "would be lost. Say, in character, what is missing: that you do "
            "not even know who they are yet, or that they have not actually "
            "asked you properly. Steer them towards what IS possible."
        )
        lines.append(
            "Willingness applies ONLY to things being asked OF you, such as "
            "an invitation to come aboard. When the captain states their own "
            "decision - that they are leaving, or attacking - it is not yours "
            "to refuse: set willing to true and react in character."
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

        # The action is DERIVED, not taken from the model. Asking it to fill an
        # action field meant it would agree warmly in prose and leave the field
        # null, so the agreement never happened. Two simple questions - what
        # does this mean, and are you willing - are far more reliable, and the
        # code joins them up.
        matched = payload.get("matches_ref")
        if isinstance(matched, str) and matched.isdigit():
            matched = int(matched)
        if not isinstance(matched, int):
            matched = None

        willing = payload.get("willing")
        if not isinstance(willing, bool):
            willing = matched is not None

        action = matched if (matched is not None and willing) else None

        remember = payload.get("remember")
        if not isinstance(remember, str) or not remember.strip():
            remember = None

        return ConverseResponse(
            id=request.id,
            spoken_text=str(payload.get("spoken_text", "")).strip(),
            action=action,
            remember=remember,
        )
