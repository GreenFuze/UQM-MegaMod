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

from uqm_ai.dialogue import DialogueFile
from uqm_ai.persona import FWIFFO, PromptBuilder
from uqm_ai.phrase_table import PhraseTable, StringsHeader
from uqm_ai.protocol import (
    ConverseRequest,
    ConverseResponse,
    ProtocolError,
    ResponseValidator,
)
from uqm_ai.providers.base import LLMProvider, ProviderError
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
                "character": "fwiffo",
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


class TestSidecarResilience:
    """The sidecar must always answer, so the game can always fall back."""

    @staticmethod
    def _run(builder: PromptBuilder, provider: LLMProvider, lines: list[str]) -> list[dict]:
        out = io.StringIO()
        Sidecar(
            builder,
            provider,
            stdin=io.StringIO("\n".join(lines) + "\n"),
            stdout=out,
            log=io.StringIO(),
        ).run()
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_handshake_reports_tts_unavailable(self, builder: PromptBuilder) -> None:
        # tts:false is a supported state, not an error: subtitles without voice.
        replies = self._run(builder, MockProvider(), ['{"type":"hello"}'])
        assert replies[0]["type"] == "ready"
        assert replies[0]["tts"] is False
        assert replies[0]["llm"] is True

    def test_provider_failure_yields_error_not_crash(self, builder: PromptBuilder) -> None:
        request = json.dumps(
            {
                "type": "converse",
                "id": 5,
                "session": {
                    "save_id": "s",
                    "character": "fwiffo",
                    "encounter": "SPATHI_PLUTO",
                },
                "player_input": "hello",
                "actions": [{"ref": 84, "text": "Come.", "terminal": True}],
            }
        )
        replies = self._run(builder, BrokenProvider(), [request])
        assert replies[0]["type"] == "error"
        assert replies[0]["id"] == 5, "error must be correlatable to the request"
        assert replies[0]["code"] == "provider_error"

    def test_malformed_input_does_not_stop_the_loop(self, builder: PromptBuilder) -> None:
        replies = self._run(
            builder, MockProvider(), ["not json", '{"type":"hello"}']
        )
        assert replies[0]["type"] == "error"
        assert replies[1]["type"] == "ready", "loop must survive bad input"

    def test_unknown_message_type_is_reported(self, builder: PromptBuilder) -> None:
        replies = self._run(builder, MockProvider(), ['{"type":"launch_missiles","id":3}'])
        assert replies[0]["type"] == "error"
        assert replies[0]["id"] == 3
