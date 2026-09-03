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

from .. import gamelog
from ..protocol import ConverseRequest, ConverseResponse, NarrateRequest
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
 "promises_action": <true if your spoken_text agrees to DO something asked of you>,
 "spoken_text": "<what you say, first person, in character>",
 "remember": "<one short line worth recalling next time, or null>"}

matches_ref is about MEANING, not agreement: if the captain is inviting you
along, that is the invitation ref whether or not you accept. willing is your
decision about it. Answer both honestly - if you say yes in spoken_text but
set willing to false, nothing happens and the moment is lost.
""".strip()


# Fields of the reply contract. Text carrying any of these is the model
# trying to answer in JSON, however badly formed, and must never be spoken.
_SCHEMA_KEYS = ('"matches_ref"', '"spoken_text"', '"willing"',
                '"promises_action"', '"remember"')


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("{", "[")) or any(
        key in text for key in _SCHEMA_KEYS
    )


def _salvage(text: str):
    """Recover the reply object from output with prose wrapped around it.

    The model is asked for a bare object and mostly obliges, but a preamble
    or a trailing remark makes json.loads fail on the whole string. Scanning
    for the first balanced {...} recovers the answer instead of discarding a
    perfectly good reply - or worse, speaking it verbatim.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == chr(92):
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        found = json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(found, dict):
                        return found
                    break
        start = text.find("{", start + 1)
    return None


# How many times the model may be asked for the object before giving up: the
# first attempt plus two corrections.
_MAX_JSON_ATTEMPTS = 3

# The reply contract as types rather than prose, so a reply can be CHECKED
# against it instead of hoped about. Each entry is the wording used to tell
# the model what the field should have been.
_SCHEMA = {
    "matches_ref": "an integer ref, or null",
    "willing": "true or false",
    "promises_action": "true or false",
    "spoken_text": "a non-empty string",
    "remember": "a short string, or null",
}


def _body(raw: str) -> str:
    """The reply text with any code fence stripped."""
    text = (raw or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fenced.group(1).strip() if fenced else text


def _schema_errors(payload: dict) -> tuple[str, ...]:
    """Every way this object fails the contract, named individually.

    Reported per field rather than as one verdict, because the correction is
    sent back to the model and a specific fault is far likelier to be fixed
    than "your JSON was wrong".
    """
    problems = []

    for field, expected in _SCHEMA.items():
        if field not in payload:
            problems.append(f"{field} was missing; it must be {expected}")
            continue

        value = payload[field]
        if field in ("willing", "promises_action"):
            # Checked before the int fields: in Python a bool IS an int, so
            # testing the other way round lets true through as a ref.
            ok = isinstance(value, bool)
        elif field == "matches_ref":
            ok = (
                value is None
                or (isinstance(value, int) and not isinstance(value, bool))
                or (isinstance(value, str) and value.isdigit())
            )
        elif field == "spoken_text":
            ok = isinstance(value, str) and value.strip() != ""
        else:
            ok = value is None or isinstance(value, str)

        if not ok:
            problems.append(f"{field} was {value!r}; it must be {expected}")

    return tuple(problems)


def _check(raw: str) -> tuple[dict | None, tuple[str, ...]]:
    """Parse a reply and measure it against the contract.

    Returns the object and what is wrong with it. No problems means it can be
    used; problems mean it is worth one more round trip.

    Genuine prose - no JSON attempted at all - reports NO problems, because
    that path already degrades safely to plain conversation and re-asking
    would spend two model calls to arrive back where it started.
    """
    text = _body(raw)
    if not text:
        return None, ("the reply was empty",)

    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError):
        payload = _salvage(text)

    if payload is None:
        if _looks_like_json(text):
            return None, (
                "the reply was not valid JSON and could not be parsed",
            )
        return None, ()

    return payload, _schema_errors(payload)


def _correction(problems: tuple[str, ...]) -> str:
    """What to send back so the model can fix its own reply."""
    faults = "\n".join(f"- {problem}" for problem in problems)
    return (
        (chr(10) * 2)
        + "Your previous reply did not fit the required JSON object:\n"
        + faults
        + "\nSend the whole object again, correctly this time: one JSON "
        "object, every field present, no prose around it and no code fence."
    )


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

        # A JSON object was asked for, so the answer is checked against the
        # contract before anything is done with it, and a reply that does not
        # fit goes back with the exact fault named. The game SPEAKS whatever
        # arrives here, so the two outcomes of giving up are a lost turn or a
        # JSON blob in the character's mouth - both worse than another round
        # trip. Two corrections, then take what we have.
        prompt = user_prompt
        for attempt in range(_MAX_JSON_ATTEMPTS):
            try:
                raw = anyio.run(self._complete, system_prompt, prompt)
            except Exception as exc:  # noqa: BLE001 - surfaced as an error
                raise ProviderError(self._describe(exc, self._last_result)) from exc

            problems = _check(raw)[1]
            if not problems:
                break

            gamelog.emit(
                f"claude: malformed reply on attempt {attempt + 1} of "
                f"{_MAX_JSON_ATTEMPTS}: {'; '.join(problems)}"
            )
            prompt = user_prompt + _correction(problems)

        response, promises = self._parse(raw, request)

        # A reply that agrees to do something while selecting nothing for the
        # game to act on is the one failure a player reads as broken: the
        # character says "I will join you" and nothing happens. Instructions
        # alone did not prevent it, so the contradiction is detected here and
        # the model gets one chance to resolve it.
        if promises and response.action is None:
            retry = (
                user_prompt
                + (chr(10) * 2) + "Your previous reply agreed to do something but "
                "selected no ref, so NOTHING WOULD HAPPEN: the captain would "
                "see you promise and then not act. Either set matches_ref to "
                "the line that makes it real, with willing true, or do not "
                "agree at all - say in character what is missing, or why you "
                "decline."
            )
            try:
                raw = anyio.run(self._complete, system_prompt, retry)
                retried, still_promises = self._parse(raw, request)
                if retried.action is not None or not still_promises:
                    return retried
            except Exception:  # noqa: BLE001 - keep the first reply on failure
                pass

        return response


    def summarise(self, system_prompt: str, transcript: str) -> str:
        """One line to remember a finished conversation by.

        Runs on a background thread after the player has left, so a failure
        here loses a memory and nothing else - it never reaches a turn.
        """
        try:
            raw = anyio.run(self._complete, system_prompt, transcript)
        except Exception as exc:  # noqa: BLE001 - never fatal
            raise ProviderError(self._describe(exc, self._last_result)) from exc

        return (raw or "").strip()

    def narrate(self, request: NarrateRequest, system_prompt: str) -> str:
        prompt = self._build_narrate_prompt(request)

        try:
            raw = anyio.run(self._complete, system_prompt, prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced to the game as an error
            raise ProviderError(self._describe(exc, self._last_result)) from exc

        text = (raw or "").strip()
        if not text:
            raise ProviderError("claude returned no text for narrate")

        # No JSON here. There is no decision left to make, so asking for a
        # structured object would only add a way for the turn to fail.
        #
        # If one arrives anyway, salvage the spoken words from it and refuse
        # the rest. The game speaks whatever this returns, and failing the
        # call is recoverable - it falls back to the authored text, which is
        # always a correct answer - while speaking a JSON blob is not.
        if _looks_like_json(text):
            salvaged = _salvage(text) or {}
            spoken = str(salvaged.get("spoken_text", "")).strip()
            if not spoken:
                raise ProviderError(
                    "claude answered narrate with JSON; refusing to speak it"
                )
            return spoken

        return text

    @staticmethod
    def _build_narrate_prompt(request: NarrateRequest) -> str:
        return "\n".join(
            [
                "The captain just said to you:",
                f'"{request.player_input}"',
                "",
                "This is what you say back. It is already settled - it is what "
                "actually happens, not a suggestion:",
                "",
                request.authored_text,
                "",
                "Say exactly that, in your own voice, as though you had just "
                "thought of it. You may change the wording, the rhythm and the "
                "length, and you may react to what the captain actually said.",
                "",
                "You may NOT change what it means. If it is a refusal, you are "
                "refusing. If it is agreement, you are agreeing. If it names a "
                "fact, that fact is true. Do not soften a no into a maybe, and "
                "never turn a no into a yes: the game has already acted on the "
                "meaning, so a reply that says otherwise leaves the captain "
                "believing something that did not happen.",
                "",
                "Add no new facts - no history, names or places that are "
                "not above or already said by you.",
                "",
                "Reply with the spoken words only. No JSON, no quotation marks "
                "around the whole thing, no narration of your actions.",
            ]
        )

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
            note = ""
            if action.is_consequential:
                note = " [CONSEQUENTIAL - changes everything]"
            elif action.changes_nothing:
                note = " [you have answered this already; it changes nothing]"
            lines.append(f'  {action.ref} = the captain means: "{action.text}"{note}')
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
            "A line marked [CONSEQUENTIAL - changes everything] is not "
            "reversible. Match one ONLY if the captain plainly and "
            "unmistakably says that thing - never because his words touch the "
            "same subject. Someone who mentions killing, weapons or a past "
            "attack while ARGUING, defending himself, asking a rhetorical "
            "question, or reasoning about what you believe is having a "
            "discussion, not making a declaration. If you find yourself about "
            "to write 'if you have decided to attack me', you have not been "
            "told that he has: choose null, or a line that opens something "
            "up, and let him say it outright if he means it."
        )
        lines.append(
            "A line marked [you have answered this already] is one you have "
            "been asked before and answered, and the answer has not changed. "
            "You are NOT going to end up agreeing to it this time either, "
            "however well the captain argues. Still match it if that is "
            "plainly what he means - he deserves a real answer rather than "
            "silence - but promise nothing, and refuse the way you would "
            "actually refuse: frightened, over-explaining, faintly "
            "apologetic, and steering him towards something you CAN talk "
            "about."
        )
        lines.append(
            "When the captain's words fit more than one line, prefer the one "
            "that is NOT marked. A captain who asks you a question and also "
            "makes a plea has done both, and the plea is the part you have "
            "already answered - so answer the question. Unmarked lines are "
            "the ones that take the conversation somewhere; keep returning "
            "to a marked one and the two of you simply circle."
        )
        lines.append(
            "Never mention any of this to the captain. He cannot see the "
            "lines, the numbers or the markings, and a character who talks "
            "about them stops being a character. Speak only as yourself."
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
            "Say only what you actually know. Do not invent history, "
            "names, places or events that were not given to you above. If you "
            "do not know something, say so, change the subject, or lie in a "
            "way that is obviously you being evasive."
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
            payload = _salvage(text)

        if payload is None:
            # Genuine prose. Degrading to pure conversation is the safe
            # direction: the player still gets a reply and no state changes.
            #
            # But only if it really is prose. _salvage refuses anything that
            # looks like the schema, because the game SPEAKS whatever arrives
            # here (comm.c:1870), and a JSON blob delivered in a character's
            # voice is the worst outcome available - worse than falling back
            # to the menu, which at least still plays.
            if _looks_like_json(text):
                raise ProviderError(
                    "claude returned unparseable JSON; refusing to speak it"
                )
            return ConverseResponse(
                id=request.id, spoken_text=text, action=None, remember=None
            ), False

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

        chosen = next((a for a in request.actions if a.ref == matched), None)

        # Willingness gates only the lines that lead somewhere. One that
        # returns to the same point in the conversation cannot commit him to
        # anything - the encounter answers it in its own authored words
        # either way - and withholding it is how this used to deadlock:
        # nothing advanced, so the line that WOULD let him join never became
        # available and recruitment was impossible.
        if chosen is None:
            action = None
        elif chosen.stays_here or willing:
            action = chosen.ref
        else:
            action = None

        remember = payload.get("remember")
        if not isinstance(remember, str) or not remember.strip():
            remember = None

        promises = bool(payload.get("promises_action", False))

        return ConverseResponse(
            id=request.id,
            spoken_text=str(payload.get("spoken_text", "")).strip(),
            action=action,
            remember=remember,
        ), promises
