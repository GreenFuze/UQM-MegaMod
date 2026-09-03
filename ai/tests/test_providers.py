"""The provider layer must behave the same whoever is answering.

Everything the player experiences - the reply contract, the schema check and
its retries, the refusal to speak a JSON blob - lives in ConversationProvider
and is shared. A backend supplies only _complete. These tests pin that seam,
and cover the OpenAI-compatible backend that makes a free local model, and
therefore playing without an account, possible at all.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from uqm_ai.providers.base import ProviderError
from uqm_ai.providers.conversation import ConversationProvider
from uqm_ai.providers.openai_compat import PRESETS, OpenAICompatProvider


class TestSharedBehaviourIsShared:
    def test_claude_uses_the_common_base(self) -> None:
        from uqm_ai.providers.claude import ClaudeProvider

        assert issubclass(ClaudeProvider, ConversationProvider)

    def test_openai_uses_the_common_base(self) -> None:
        assert issubclass(OpenAICompatProvider, ConversationProvider)

    def test_the_prompts_live_on_the_base_not_a_vendor(self) -> None:
        """A second backend must not mean a second copy of the prompt."""
        from uqm_ai.providers.claude import ClaudeProvider

        assert (ClaudeProvider._build_user_prompt
                is ConversationProvider._build_user_prompt)
        assert (OpenAICompatProvider._build_narrate_prompt
                is ConversationProvider._build_narrate_prompt)
        assert OpenAICompatProvider._parse is ConversationProvider._parse


class TestPresets:
    def test_local_needs_no_key(self, monkeypatch) -> None:
        """The whole point: playable with no account and no billing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("UQMAI_API_KEY", raising=False)
        monkeypatch.delenv("UQMAI_BASE_URL", raising=False)
        monkeypatch.delenv("UQMAI_MODEL", raising=False)

        provider = OpenAICompatProvider(preset="local")
        assert provider._base == "http://localhost:11434/v1"
        assert "llama" in provider.name

    def test_openai_without_a_key_says_so_at_startup(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("UQMAI_API_KEY", raising=False)

        with pytest.raises(ProviderError) as caught:
            OpenAICompatProvider(preset="openai")
        message = str(caught.value)
        assert "OPENAI_API_KEY" in message
        # It must point at the free route, not just refuse.
        assert "--provider local" in message or "local model" in message

    def test_environment_overrides_the_preset(self, monkeypatch) -> None:
        monkeypatch.setenv("UQMAI_BASE_URL", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("UQMAI_MODEL", "mistral-7b")

        provider = OpenAICompatProvider(preset="local")
        assert provider._base == "http://127.0.0.1:8080/v1"
        assert provider._model == "mistral-7b"

    def test_an_unknown_preset_is_refused(self) -> None:
        with pytest.raises(ProviderError):
            OpenAICompatProvider(preset="gemini")

    def test_every_preset_is_reachable_from_the_cli(self) -> None:
        """Presets and --provider choices must not drift apart."""
        import inspect

        from uqm_ai import __main__ as entry

        source = inspect.getsource(entry)
        for preset in PRESETS:
            assert f'"{preset}"' in source, f"{preset} not offered by the CLI"


class TestCompletion:
    @staticmethod
    def _provider(monkeypatch):
        monkeypatch.delenv("UQMAI_BASE_URL", raising=False)
        monkeypatch.delenv("UQMAI_MODEL", raising=False)
        return OpenAICompatProvider(preset="local")

    def test_a_reply_is_read_from_the_first_choice(self, monkeypatch) -> None:
        provider = self._provider(monkeypatch)
        sent = {}

        class FakeReply:
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "Hello, Captain."}}]}
                ).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(request, timeout=None):
            sent["url"] = request.full_url
            sent["body"] = json.loads(request.data.decode())
            sent["headers"] = request.headers
            return FakeReply()

        monkeypatch.setattr(
            "uqm_ai.providers.openai_compat.urllib.request.urlopen", fake_urlopen
        )
        assert provider._complete("system", "user") == "Hello, Captain."
        assert sent["url"] == "http://localhost:11434/v1/chat/completions"
        assert sent["body"]["messages"][0] == {"role": "system", "content": "system"}
        assert sent["body"]["messages"][1] == {"role": "user", "content": "user"}
        assert sent["body"]["stream"] is False

    def test_no_authorization_header_without_a_key(self, monkeypatch) -> None:
        """A local server given a bogus Bearer token can reject the request."""
        provider = self._provider(monkeypatch)
        seen = {}

        class FakeReply:
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "x"}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(request, timeout=None):
            seen.update(request.headers)
            return FakeReply()

        monkeypatch.setattr(
            "uqm_ai.providers.openai_compat.urllib.request.urlopen", fake_urlopen
        )
        provider._complete("s", "u")
        assert not any(k.lower() == "authorization" for k in seen)

    def test_an_empty_choices_list_is_an_error_not_silence(self, monkeypatch) -> None:
        provider = self._provider(monkeypatch)

        class FakeReply:
            def read(self): return json.dumps({"choices": []}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(
            "uqm_ai.providers.openai_compat.urllib.request.urlopen",
            lambda request, timeout=None: FakeReply(),
        )
        with pytest.raises(ProviderError):
            provider._complete("s", "u")


class TestFailuresAreLegible:
    """A player should learn what to fix, not see a stack trace's worth of URL."""

    def test_a_server_that_is_not_running_names_the_fix(self) -> None:
        text = OpenAICompatProvider._describe(
            urllib.error.URLError("Connection refused")
        )
        assert "ollama serve" in text.lower()
        assert "--no-ai" in text

    def test_a_rejected_key_is_named_as_such(self) -> None:
        text = OpenAICompatProvider._describe(
            urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        )
        assert "credential" in text.lower() or "key" in text.lower()

    def test_a_wrong_model_points_at_the_setting(self) -> None:
        text = OpenAICompatProvider._describe(
            urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        )
        assert "UQMAI_MODEL" in text
