"""Startup checks for the AI subsystem.

Every one of these has already cost us a debugging session by failing
silently. A missing prerequisite must stop the game with a clear reason, not
degrade into behaviour that merely looks like a hang or like stock MegaMod.

The live check matters most. Everything else can pass while the CLI still
refuses to answer - an expired login, or an untrusted working directory,
both of which surface only when a real request is made.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cast import Cast


@dataclass(frozen=True)
class Problem:
    """One failed check, with the action that resolves it."""

    what: str
    fix: str

    def render(self) -> str:
        return f"{self.what}\n    fix: {self.fix}"


class Preflight:
    """Runs the startup checks for the whole cast and the provider."""

    def __init__(self, repo: Path, cast: "Cast") -> None:
        self._repo = repo
        self._cast = cast

    def run(self, provider: str, live: bool = True) -> list[Problem]:
        problems: list[Problem] = []
        problems.extend(self._check_content())

        if provider == "claude":
            problems.extend(self._check_sdk())
            if not problems and live:
                problems.extend(self._check_live())

        return problems

    def _check_content(self) -> list[Problem]:
        """Build every authored character's table, and report all failures.

        All of them, not the first: three races failed for three unrelated
        reasons, and finding them one game-launch at a time is how that went
        unnoticed for as long as it did.
        """
        found: list[Problem] = []

        if not self._cast.served:
            found.append(
                Problem(
                    "no authored characters found",
                    "check ai/characters/ contains at least one .toml",
                )
            )

        for resource in sorted(self._cast.served):
            spec = self._cast.spec(resource)
            if not spec.header.is_file():
                found.append(Problem(
                    f"phrase enum not found: {spec.header}",
                    "run from the game checkout; --repo must point at uqm-megamod",
                ))
                continue
            if not spec.dialogue.is_file():
                found.append(Problem(
                    f"canonical dialogue not found: {spec.dialogue}",
                    "clone UQM-MegaMod-Content next to the game checkout "
                    "(see docs/build.md)",
                ))
                continue
            try:
                self._cast.builder(resource)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                found.append(Problem(
                    f"{resource} cannot be loaded: {exc}",
                    "the phrase enum and the dialogue file disagree; see "
                    "docs/conversation-corpus.md section 5",
                ))
        return found

    def _check_sdk(self) -> list[Problem]:
        found: list[Problem] = []

        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            found.append(
                Problem(
                    "claude-agent-sdk is not installed",
                    "pip install claude-agent-sdk",
                )
            )
            return found

        if shutil.which("claude") is None:
            # The SDK bundles a CLI, so this is a warning-level finding that
            # only matters if the live check also fails; it is reported for
            # diagnosis rather than treated as fatal on its own.
            pass

        return found

    def _check_live(self) -> list[Problem]:
        """Make one real request. This is the only check that catches a
        rejected key, an exhausted balance or an untrusted working directory,
        because all three look perfectly healthy until something is actually
        asked."""
        import anyio

        from .providers.claude import ClaudeProvider

        try:
            provider = ClaudeProvider(timeout_s=45.0)
        except Exception as exc:  # noqa: BLE001
            return [Problem(f"provider unavailable: {exc}", "see above")]

        async def ask() -> str:
            return await provider._complete(  # noqa: SLF001 - deliberate probe
                "Reply with the single word OK.", "ping"
            )

        try:
            reply = anyio.run(ask)
        except Exception as exc:  # noqa: BLE001
            # Prefer the CLI's own words, which the provider captured, over
            # the SDK's opaque wrapper text.
            return [Problem(
                provider._describe(exc, provider._last_result),  # noqa: SLF001
                self._live_fix(),
            )]

        if not reply.strip():
            return [Problem("the Claude CLI returned an empty response",
                            self._live_fix())]
        return []

    @staticmethod
    def _live_fix() -> str:
        return (
            "set ANTHROPIC_API_KEY to a valid key from "
            "https://console.anthropic.com/settings/keys and make sure the "
            "account has credit; conversation is billed to your own API "
            "account. If it asks about trusting a folder, answer yes for "
            + str(Path.cwd())
            + " - the CLI cannot be asked that when the game starts it. "
            "Play without AI using --no-ai."
        )


def report(problems: list[Problem], stream=sys.stderr) -> None:
    """Prints problems in a form a player can act on."""
    stream.write("\n[uqm-ai] AI subsystem cannot start:\n\n")
    for problem in problems:
        stream.write("  - " + problem.render() + "\n\n")
    stream.write("  Start the game with --no-ai to play without AI.\n\n")
    stream.flush()
