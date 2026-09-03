"""Contract tests for the AI layer.

These cover the failure modes named in the project brief: an invalid action
must not mutate state, malformed output must not destabilise the game, a
canonical action must behave like the equivalent menu choice, and the
conversation must survive both the LLM and the TTS being unavailable.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from uqm_ai.cast import Cast
from uqm_ai.dialogue import DialogueFile
from uqm_ai.persona import FWIFFO, PromptBuilder
from uqm_ai.phrase_table import PhraseTable, StringsHeader
from uqm_ai.protocol import (
    FLOW_DEPARTS,
    FLOW_SAME_NODE,
    ConverseRequest,
    ConverseResponse,
    NarrateRequest,
    ProtocolError,
    ResponseValidator,
)
from uqm_ai.providers.base import LLMProvider, ProviderError
from uqm_ai.providers.claude import ClaudeProvider
from uqm_ai.providers.mock import MockProvider
from uqm_ai.sidecar import Sidecar

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO.parent / "uqm-megamod-content"


def _table() -> PhraseTable:
    return PhraseTable(
        StringsHeader(REPO / "src/uqm/comm/spathi/strings.h"),
        DialogueFile(CONTENT / "base/comm/spathi/spathi.txt"),
    )


TABLE = _table()
KEY_BY_REF = {e.enum_value: e.key for e in TABLE.entries}


def ref_of(key: str) -> int:
    return TABLE.by_key(key).enum_value


def make_request(text: str, actions: list[str], **context) -> ConverseRequest:
    """Build a request using the real RESPONSE_REF values the game would send."""
    request = ConverseRequest.from_json(
        {
            "type": "converse",
            "id": 1,
            "session": {
                "save_id": "slot1",
                "character": "comm.spathi.dialogue",
                "encounter": "SPATHI_PLUTO",
            },
            "player_input": text,
            "actions": [
                {"ref": ref_of(a), "text": TABLE.by_key(a).text, "terminal": False}
                for a in actions
            ],
            "context": context,
        }
    )
    return request.with_resolved_keys(KEY_BY_REF.get)


@pytest.fixture(scope="module")
def builder() -> PromptBuilder:
    return PromptBuilder(FWIFFO, TABLE)


@pytest.fixture(scope="module")
def cast() -> Cast:
    """The real cast: the sidecar now picks a character per request."""
    return Cast(REPO, CONTENT)


class TestPhraseTable:
    def test_enum_and_dialogue_agree(self, builder: PromptBuilder) -> None:
        # A silent misalignment would attribute the wrong words to the wrong
        # action, so the join must be exact rather than merely plausible.
        entry = builder.canonical_lines(("I_FWIFFO",))[0]
        assert entry.key == "I_FWIFFO"
        assert entry.voice_clip == "spathi-001.ogg"
        assert "Fwiffo" in entry.text


class TestValidation:
    def test_invalid_action_is_rejected_and_prose_survives(self) -> None:
        request = make_request("do the thing", ["join_us"])
        validator = ResponseValidator(request)

        result = validator.validate(
            ConverseResponse(id=1, spoken_text="ha ha", action=9999)
        )

        assert result.action is None, "unexported action must never be actioned"
        assert result.spoken_text == "ha ha"
        assert validator.rejections

    def test_stale_action_from_previous_turn_is_rejected(self) -> None:
        # SelectResponse clears response_list before dispatch, so an action
        # offered last turn is not offered now.
        request = make_request("come with me", ["what_about_yourself"])
        result = ResponseValidator(request).validate(
            ConverseResponse(id=1, spoken_text="ok", action=ref_of("join_us"))
        )
        assert result.action is None

    def test_empty_spoken_text_is_an_error(self) -> None:
        request = make_request("hi", ["join_us"])
        with pytest.raises(ProtocolError):
            ResponseValidator(request).validate(
                ConverseResponse(id=1, spoken_text="   ")
            )

    def test_oversized_fields_are_truncated_not_rejected(self) -> None:
        request = make_request("hi", ["join_us"])
        validator = ResponseValidator(request)
        result = validator.validate(
            ConverseResponse(id=1, spoken_text="x" * 5000, remember="y" * 5000)
        )
        assert len(result.spoken_text) == 2000
        assert result.remember is not None and len(result.remember) == 400

    def test_remember_is_folded_like_spoken_text(self) -> None:
        # It is quoted back into later prompts and is bound for the save, so
        # it must not smuggle in punctuation the game cannot draw. This
        # reached the wire in play: an em-dash in remember, on a stdout that
        # Windows had defaulted to cp1252.
        request = make_request("hi", ["join_us"])
        result = ResponseValidator(request).validate(
            ConverseResponse(
                id=1,
                spoken_text="fine",
                remember="He offered charm — I refused…",
            )
        )
        assert result.remember is not None
        assert all(ord(ch) < 128 for ch in result.remember), result.remember

    def test_request_without_actions_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            make_request("hi", [])


class TestMockProvider:
    def test_selects_canonical_action_when_offered(self) -> None:
        response = MockProvider().generate(
            make_request("Fwiffo, come with me.", ["join_us", "changed_mind"]), ""
        )
        assert response.action == ref_of("join_us")

    def test_fails_closed_when_intent_is_not_offered(self) -> None:
        response = MockProvider().generate(
            make_request("Fwiffo, come with me.", ["what_about_yourself"]), ""
        )
        assert response.action is None

    def test_free_conversation_changes_nothing(self) -> None:
        # The point of the project: talking must be possible without any
        # deterministic state transition.
        response = MockProvider().generate(
            make_request("What do you actually eat?", ["join_us"]), ""
        )
        assert response.action is None
        assert response.spoken_text


class TestFlowGating:
    """Willingness gates departures only.

    A response wired back to the node that registered it cannot commit the
    player to anything: the encounter answers it in its own authored words
    either way. Withholding those is how the conversation used to deadlock -
    nothing advanced, so the line that would let Fwiffo join never became
    available and recruitment was impossible.
    """

    @staticmethod
    def _request(flow: int, repeated: bool = False) -> ConverseRequest:
        request = ConverseRequest.from_json(
            {
                "type": "converse",
                "id": 1,
                "player_input": "Come with us.",
                "actions": [
                    {
                        "ref": ref_of("join_us"),
                        "text": TABLE.by_key("join_us").text,
                        "terminal": False,
                        "flow": flow,
                        "repeated": repeated,
                    },
                    {
                        "ref": ref_of("what_doing_on_pluto_1"),
                        "text": TABLE.by_key("what_doing_on_pluto_1").text,
                        "terminal": False,
                        "flow": FLOW_SAME_NODE,
                    },
                ],
            }
        )
        return request.with_resolved_keys(KEY_BY_REF.get)

    @staticmethod
    def _reply(willing: bool) -> str:
        return json.dumps(
            {
                "matches_ref": ref_of("join_us"),
                "willing": willing,
                "promises_action": False,
                "spoken_text": "Absolutely not.",
                "remember": None,
            }
        )

    def test_unwilling_still_advances_a_line_that_loops_back(self) -> None:
        request = self._request(FLOW_SAME_NODE)
        response, _ = ClaudeProvider._parse(self._reply(False), request)
        assert response.action == ref_of("join_us"), (
            "a loop-back must fire regardless of willingness, or the "
            "encounter never gets to give its own refusal"
        )

    def test_unwilling_withholds_a_line_that_departs(self) -> None:
        request = self._request(FLOW_DEPARTS)
        response, _ = ClaudeProvider._parse(self._reply(False), request)
        assert response.action is None, (
            "refusing must still be possible for anything that leaves this "
            "point in the conversation"
        )

    def test_willing_fires_a_departure(self) -> None:
        request = self._request(FLOW_DEPARTS)
        response, _ = ClaudeProvider._parse(self._reply(True), request)
        assert response.action == ref_of("join_us")

    @staticmethod
    def _action_line(prompt: str, key: str) -> str:
        """The one listed-action line for a ref.

        Asserted on rather than the whole prompt: the standing guidance names
        every marking, so a whole-prompt search always passes and proves
        nothing about what this particular line was told.
        """
        prefix = f"  {ref_of(key)} = "
        return next(l for l in prompt.splitlines() if l.startswith(prefix))

    def test_only_a_repeated_line_is_marked_as_going_nowhere(self) -> None:
        # Structure alone cannot tell join_us from what_doing_on_pluto_1 -
        # both return to the same handler - so an unrepeated line must carry
        # no such mark. Marking the question would tell the model that the one
        # thing which actually advances the encounter is pointless.
        fresh = ClaudeProvider._build_user_prompt(self._request(FLOW_SAME_NODE))
        assert "answered this already" not in self._action_line(fresh, "join_us")

        again = ClaudeProvider._build_user_prompt(
            self._request(FLOW_SAME_NODE, repeated=True)
        )
        assert "answered this already" in self._action_line(again, "join_us")
        assert "answered this already" not in self._action_line(
            again, "what_doing_on_pluto_1"
        )

    def test_a_departure_is_marked_consequential(self) -> None:
        prompt = ClaudeProvider._build_user_prompt(self._request(FLOW_DEPARTS))

        assert "[CONSEQUENTIAL - changes everything]" in self._action_line(
            prompt, "join_us"
        )
        assert "[CONSEQUENTIAL" not in self._action_line(
            prompt, "what_doing_on_pluto_1"
        )
        assert "never because his words touch the same subject" in prompt

    def test_mechanics_are_never_revealed_to_the_player(self) -> None:
        prompt = ClaudeProvider._build_user_prompt(
            self._request(FLOW_SAME_NODE, repeated=True)
        )
        assert "Never mention any of this to the captain" in prompt


class TestSpoilerGating:
    def test_locked_secret_never_reaches_the_prompt(self, builder: PromptBuilder) -> None:
        prompt = builder.render(permitted_keys=("I_FWIFFO",))
        assert "COORDINATES_ARE" not in prompt
        assert "241.6" not in prompt

    def test_unlocked_secret_is_available_to_speak(self, builder: PromptBuilder) -> None:
        # Gating is about timing, not permanent redaction: once the game
        # unlocks it, Fwiffo must be able to give it up in conversation.
        prompt = builder.render(permitted_keys=("COORDINATES_ARE",))
        assert "Spathiwa" in prompt

    def test_unknown_knowledge_keys_are_ignored(self, builder: PromptBuilder) -> None:
        # The game is the authority on what exists; an unrecognised key is not
        # a reason to fail a conversation.
        assert builder.canonical_lines(("NO_SUCH_PHRASE",)) == ()


class BrokenProvider(LLMProvider):
    """A provider that always fails, standing in for a dead or absent model."""

    @property
    def name(self) -> str:
        return "broken"

    def generate(self, request, system_prompt):  # noqa: ANN001
        raise ProviderError("model not loaded")

    def narrate(self, request, system_prompt):  # noqa: ANN001
        raise ProviderError("model not loaded")


class TestSidecarResilience:
    """The sidecar must always answer, so the game can always fall back."""

    @staticmethod
    def _run(cast: Cast, provider: LLMProvider, lines: list[str]) -> list[dict]:
        out = io.StringIO()
        Sidecar(
            cast,
            provider,
            stdin=io.StringIO("\n".join(lines) + "\n"),
            stdout=out,
            log=io.StringIO(),
        ).run()
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_handshake_reports_tts_unavailable(self, cast: Cast) -> None:
        # tts:false is a supported state, not an error: subtitles without voice.
        replies = self._run(cast, MockProvider(), ['{"type":"hello"}'])
        assert replies[0]["type"] == "ready"
        assert replies[0]["tts"] is False
        assert replies[0]["llm"] is True

    def test_provider_failure_yields_error_not_crash(self, cast: Cast) -> None:
        request = json.dumps(
            {
                "type": "converse",
                "id": 5,
                "session": {
                    "save_id": "s",
                    "character": "comm.spathi.dialogue",
                    "encounter": "SPATHI_PLUTO",
                },
                "player_input": "hello",
                "actions": [{"ref": 84, "text": "Come.", "terminal": True}],
            }
        )
        replies = self._run(cast, BrokenProvider(), [request])
        assert replies[0]["type"] == "error"
        assert replies[0]["id"] == 5, "error must be correlatable to the request"
        assert replies[0]["code"] == "provider_error"

    def test_narrate_carries_the_encounter_outcome(self, cast: Cast) -> None:
        # The defect this guards against: a generated line agreeing to
        # something the encounter had just refused. The refusal is authored
        # text produced by the handler, and it must reach the player.
        refusal = "I am not going anywhere with you. Absolutely not."
        request = json.dumps(
            {
                "type": "narrate",
                "id": 9,
                "session_save_id": "s",
                "session_character": "comm.spathi.dialogue",
                "player_input": "Join us, we will keep you safe.",
                "authored_text": refusal,
                "spoken_refs": [],
            }
        )
        replies = self._run(cast, MockProvider(), [request])

        assert replies[0]["type"] == "narrate"
        assert replies[0]["id"] == 9
        # The mock rewords nothing, so the outcome must survive verbatim
        # apart from the subtitle pagination the game needs.
        assert replies[0]["spoken_text"].replace("\n", " ") == refusal
        assert "action" not in replies[0], "narrate must never carry an action"

    def test_generated_text_is_folded_to_renderable_ascii(
        self, cast: Cast
    ) -> None:
        # UQM's bitmap fonts have no glyph for typographic punctuation, so a
        # model writing an em-dash by habit puts a box in the subtitle.
        request = json.dumps(
            {
                "type": "narrate",
                "id": 13,
                "session_character": "comm.spathi.dialogue",
                "player_input": "hello",
                "authored_text": "I am— as it were —“fine”…",
            }
        )
        replies = self._run(cast, MockProvider(), [request])

        spoken = replies[0]["spoken_text"]
        assert all(ord(ch) < 128 for ch in spoken), spoken
        assert '"fine"' in spoken
        assert "..." in spoken

    def test_narrate_without_authored_text_is_rejected(
        self, cast: Cast
    ) -> None:
        # An empty outcome would leave the model free to invent one, which is
        # the whole failure this path exists to close.
        replies = self._run(
            cast,
            MockProvider(),
            ['{"type":"narrate","id":11,"player_input":"hi","authored_text":""}'],
        )
        assert replies[0]["type"] == "error"
        assert replies[0]["id"] == 11
        assert replies[0]["code"] == "protocol_error"

    def test_malformed_input_does_not_stop_the_loop(self, cast: Cast) -> None:
        replies = self._run(
            cast, MockProvider(), ["not json", '{"type":"hello"}']
        )
        assert replies[0]["type"] == "error"
        assert replies[1]["type"] == "ready", "loop must survive bad input"

    def test_unknown_message_type_is_reported(self, cast: Cast) -> None:
        replies = self._run(cast, MockProvider(), ['{"type":"launch_missiles","id":3}'])
        assert replies[0]["type"] == "error"
        assert replies[0]["id"] == 3


class TestNeverSpeakJson:
    """A JSON blob must never reach the subtitle in a character's voice.

    The game SPEAKS whatever comes back (comm.c:1870 for a failed turn,
    and the reply text otherwise). _parse used to fall back to "treat the raw
    output as prose", which is right for prose and catastrophic for
    almost-JSON: a preamble or a trailing comma made json.loads fail and the
    whole object was delivered as the character's line.
    """

    @staticmethod
    def _request():
        return make_request("What should I do next?", ["join_us"])

    def test_prose_is_still_accepted(self) -> None:
        from uqm_ai.providers.claude import ClaudeProvider

        response, _ = ClaudeProvider._parse(
            "Leave Pluto? Out there is where the monsters are!", self._request()
        )
        assert response.spoken_text.startswith("Leave Pluto?")
        assert response.action is None

    def test_json_with_a_preamble_is_salvaged(self) -> None:
        from uqm_ai.providers.claude import ClaudeProvider

        raw = (
            'Here is my reply:\n'
            '{"matches_ref": null, "willing": false, "promises_action": false,'
            ' "spoken_text": "No. I am staying here.", "remember": null}\n'
            'Let me know if you need anything else.'
        )
        response, _ = ClaudeProvider._parse(raw, self._request())
        assert response.spoken_text == "No. I am staying here."
        assert "matches_ref" not in response.spoken_text

    def test_unparseable_json_is_refused_rather_than_spoken(self) -> None:
        from uqm_ai.providers.base import ProviderError
        from uqm_ai.providers.claude import ClaudeProvider

        # Trailing comma: not valid JSON, and unmistakably not prose either.
        raw = '{"spoken_text": "hello", "willing": true,}'
        with pytest.raises(ProviderError):
            ClaudeProvider._parse(raw, self._request())

    def test_a_bare_object_still_parses(self) -> None:
        from uqm_ai.providers.claude import ClaudeProvider

        raw = (
            '{"matches_ref": null, "willing": false, "promises_action": false,'
            ' "spoken_text": "Hello there.", "remember": null}'
        )
        response, _ = ClaudeProvider._parse(raw, self._request())
        assert response.spoken_text == "Hello there."


class TestJsonSchemaIsCheckedAndRetried:
    """A malformed object is worth another round trip, not a lost turn.

    The model is asked for a JSON object, so the object is checked against
    the contract field by field. When it does not fit, the specific fault is
    named and sent back - twice, if need be - because the game speaks
    whatever finally arrives and both ways of giving up are worse than
    asking again. Seen in play: "claude returned unparseable JSON" reached
    the player as Hayes's own line.
    """

    @staticmethod
    def _good():
        return ('{"matches_ref": null, "willing": false, "promises_action":'
                ' false, "spoken_text": "Hello there.", "remember": null}')

    def test_a_good_object_has_no_problems(self) -> None:
        from uqm_ai.providers.claude import _check

        payload, problems = _check(self._good())
        assert problems == ()
        assert payload["spoken_text"] == "Hello there."

    def test_unparseable_json_is_a_problem(self) -> None:
        from uqm_ai.providers.claude import _check

        _, problems = _check('{"spoken_text": "hi", "willing": true,}')
        assert problems and "not valid JSON" in problems[0]

    def test_prose_is_not_a_problem(self) -> None:
        """Prose already degrades safely; re-asking would spend two calls."""
        from uqm_ai.providers.claude import _check

        payload, problems = _check("Out there is where the monsters are!")
        assert payload is None
        assert problems == ()

    def test_each_bad_field_is_named_separately(self) -> None:
        from uqm_ai.providers.claude import _check

        _, problems = _check('{"matches_ref": "soon", "willing": "yes",'
                             ' "promises_action": false, "spoken_text": "",'
                             ' "remember": null}')
        named = " ".join(problems)
        assert "matches_ref" in named
        assert "willing" in named
        assert "spoken_text" in named
        assert "promises_action" not in named

    def test_a_missing_field_is_reported(self) -> None:
        from uqm_ai.providers.claude import _check

        _, problems = _check('{"spoken_text": "Hello."}')
        assert any("matches_ref was missing" in p for p in problems)
        assert any("willing was missing" in p for p in problems)

    def test_true_is_not_accepted_as_a_ref(self) -> None:
        """In Python a bool IS an int, so the order of the checks matters."""
        from uqm_ai.providers.claude import _check

        _, problems = _check('{"matches_ref": true, "willing": true,'
                             ' "promises_action": false, "spoken_text": "Hi.",'
                             ' "remember": null}')
        assert any("matches_ref" in p for p in problems)

    def test_a_digit_string_ref_is_tolerated(self) -> None:
        """_parse already coerces it, so it is not worth a round trip."""
        from uqm_ai.providers.claude import _check

        _, problems = _check('{"matches_ref": "66", "willing": true,'
                             ' "promises_action": false, "spoken_text": "Aye.",'
                             ' "remember": null}')
        assert problems == ()

    def test_a_fenced_object_is_accepted(self) -> None:
        from uqm_ai.providers.claude import _check

        fence = chr(96) * 3
        _, problems = _check(fence + "json\n" + self._good() + "\n" + fence)
        assert problems == ()

    def test_the_correction_names_the_faults(self) -> None:
        from uqm_ai.providers.claude import _correction

        text = _correction(("willing was 'yes'; it must be true or false",))
        assert "willing was 'yes'" in text
        assert "again" in text.lower()

    def test_the_model_is_asked_again_and_the_fix_is_used(self) -> None:
        """The whole loop: bad reply, correction sent, good reply used."""
        from uqm_ai.providers.claude import ClaudeProvider

        prompts = []
        replies = iter(['{"spoken_text": "hi", "willing": true,}', self._good()])

        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider._model = None
        provider._timeout_s = 1.0
        provider._last_result = ""

        def fake_run(_fn, _system, prompt):
            prompts.append(prompt)
            return next(replies)

        import uqm_ai.providers.claude as mod
        original = mod.anyio.run
        mod.anyio.run = fake_run
        try:
            response = provider.generate(
                make_request("Tell me about the Sa-Matra", ["join_us"]),
                "system",
            )
        finally:
            mod.anyio.run = original

        assert len(prompts) == 2, "the model should have been asked again"
        assert "not valid JSON" in prompts[1]
        assert response.spoken_text == "Hello there."

    def test_it_gives_up_after_two_corrections(self) -> None:
        """Three calls in total, then the existing safe handling takes over."""
        from uqm_ai.providers.base import ProviderError
        from uqm_ai.providers.claude import ClaudeProvider, _MAX_JSON_ATTEMPTS

        calls = []

        provider = ClaudeProvider.__new__(ClaudeProvider)
        provider._model = None
        provider._timeout_s = 1.0
        provider._last_result = ""

        def fake_run(_fn, _system, prompt):
            calls.append(prompt)
            return '{"spoken_text": "hi", "willing": true,}'

        import uqm_ai.providers.claude as mod
        original = mod.anyio.run
        mod.anyio.run = fake_run
        try:
            with pytest.raises(ProviderError):
                provider.generate(
                    make_request("Tell me about the Sa-Matra", ["join_us"]),
                    "system",
                )
        finally:
            mod.anyio.run = original

        assert len(calls) == _MAX_JSON_ATTEMPTS == 3


class TestPromptDoesNotFightTheContract:
    """The system prompt must not issue a second, conflicting "reply with"."""

    def test_no_competing_reply_instruction(self) -> None:
        from uqm_ai.cast import Cast

        cast = Cast(REPO, CONTENT)
        prompt = cast.builder("comm.starbase.dialogue").render(permitted_keys=())

        # The user prompt says "Reply with ONLY a JSON object". A second
        # imperative here made the model split the difference and emit JSON
        # wrapped in prose.
        assert "Reply as" not in prompt
        assert "Your spoken words are" in prompt
