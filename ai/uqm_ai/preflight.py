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


@dataclass(frozen=True)
class Problem:
    """One failed check, with the action that resolves it."""

    what: str
    fix: str

    def render(self) -> str:
        return f"{self.what}\n    fix: {self.fix}"


class Preflight:
    """Runs the startup checks for one character's data and the provider."""

    def __init__(self, repo: Path, header: Path, dialogue: Path) -> None:
        self._repo = repo
        self._header = header
        self._dialogue = dialogue

    def run(self, provider: str, live: bool = True) -> list[Problem]:
        problems: list[Problem] = []
        problems.extend(self._check_content())

        if provider == "claude":
            problems.extend(self._check_sdk())
            if not problems and live:
                problems.extend(self._check_live())

        return problems

    def _check_content(self) -> list[Problem]:
        found: list[Problem] = []

        if not self._header.is_file():
            found.append(
                Problem(
                    f"phrase enum not found: {self._header}",
                    "run from the game checkout; --repo must point at uqm-megamod",
                )
            )
        if not self._dialogue.is_file():
            found.append(
                Problem(
                    f"canonical dialogue not found: {self._dialogue}",
                    "clone UQM-MegaMod-Content next to the game checkout "
                    "(see docs/build.md)",
                )
            )
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
        """Make one real request. This is the only check that catches an
        expired login or an untrusted working directory, because both look
        perfectly healthy until something is actually asked of the CLI."""
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
            "if it says you are not signed in: run 'claude' in a terminal, "
            "type /login, and finish sign-in in the browser - simply starting "
            "the CLI does not renew an expired session. If it asks about "
            "trusting a folder, answer yes for "
            + str(Path.cwd())
            + ". The CLI can be asked neither of these when the game starts it."
        )


def report(problems: list[Problem], stream=sys.stderr) -> None:
    """Prints problems in a form a player can act on."""
    stream.write("\n[uqm-ai] AI subsystem cannot start:\n\n")
    for problem in problems:
        stream.write("  - " + problem.render() + "\n\n")
    stream.write("  Start the game with --no-ai to play without AI.\n\n")
    stream.flush()
