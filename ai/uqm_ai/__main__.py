"""Entry point: python -m uqm_ai --character fwiffo

Wires a character's phrase table to a provider and serves stdio.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import IO

from . import gamelog
from .dialogue import DialogueFile
from .persona import FWIFFO, PromptBuilder
from .phrase_table import PhraseTable, StringsHeader
from .preflight import Preflight, report
from .providers.base import LLMProvider, ProviderError, TTSProvider
from .providers.mock import MockProvider
from .sidecar import Sidecar

# Characters we can serve, and where their data lives relative to the repo.
_CHARACTERS = {
    "fwiffo": (
        FWIFFO,
        "src/uqm/comm/spathi/strings.h",
        "../uqm-megamod-content/base/comm/spathi/spathi.txt",
    ),
}


def _claim_wire() -> IO[str]:
    """Take exclusive ownership of stdout for the protocol.

    Two problems, one fix.

    Libraries print. Chatterbox's watermarker announces "loaded PerthNet
    (Implicit) at step 250,000" on stdout the first time it runs, which lands
    between two NDJSON messages and corrupts the stream - the game sees a
    line that is not JSON and the turn dies. Chasing each offender is a
    losing game, so the real stdout is duplicated for our exclusive use and
    sys.stdout is pointed at stderr: anything that prints now goes to the
    log, where it belongs.

    And Python picks the console code page on Windows - cp1252 here - so any
    non-ASCII character was silently transcoded into a byte the game could
    not render, while anything the code page could not represent raised
    mid-write and took the turn with it. The protocol is UTF-8, so the
    duplicate is opened as UTF-8 rather than inheriting the console's idea.
    """
    wire = os.fdopen(os.dup(sys.stdout.fileno()), "w",
                     encoding="utf-8", newline="\n")
    sys.stdout = sys.stderr

    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", newline="\n")

    return wire


# Fwiffo's own voice, used both as the canned stand-in and as the reference
# a cloner imitates. Derived on the player's machine from the voice pack they
# already have; nothing cloned is ever shipped.
_FWIFFO_CLIP = "addons/mm-3dovoice/spathi/spathi-001.ogg"


def _build_tts(kind: str, repo: Path) -> TTSProvider:
    clip = repo.parent / "uqm-megamod-content" / _FWIFFO_CLIP

    if kind == "canned":
        from .providers.canned_tts import CannedTTS

        return CannedTTS(clip)

    if kind == "chatterbox":
        from .providers.chatterbox_tts import ChatterboxVoice

        return ChatterboxVoice(clip)

    raise ProviderError(f"unknown tts provider {kind!r}")


def main(argv: list[str] | None = None) -> int:
    wire = _claim_wire()
    gamelog.attach(wire)

    parser = argparse.ArgumentParser(prog="uqm_ai")
    parser.add_argument("--character", default="fwiffo", choices=sorted(_CHARACTERS))
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="path to the uqm-megamod checkout",
    )
    parser.add_argument("--provider", default="mock",
                        choices=["mock", "claude"])
    parser.add_argument(
        "--tts",
        default="none",
        choices=["none", "canned", "chatterbox"],
        help="'canned' replays one of the character's own clips for every "
             "line: the words do not match, which is how it proves the audio "
             "path without a model or a GPU",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="run startup checks, print any problems, and exit",
    )
    parser.add_argument(
        "--skip-live-check",
        action="store_true",
        help="skip the live provider request during preflight",
    )
    args = parser.parse_args(argv)

    profile, header_rel, dialogue_rel = _CHARACTERS[args.character]
    header = args.repo / header_rel
    dialogue = args.repo / dialogue_rel

    # Checked before anything else: each of these has previously failed in a
    # way that looked like the game hanging rather than like a missing
    # prerequisite.
    problems = Preflight(args.repo, header, dialogue).run(
        args.provider, live=not args.skip_live_check
    )
    if problems:
        report(problems)
        # Also send it over the protocol, so the game can show the reason in
        # its own error dialog. stderr is inherited and unreadable to it, and
        # a dialog saying "see above" helps nobody.
        summary = " | ".join(f"{p.what} ({p.fix})" for p in problems)
        wire.write(
            json.dumps({"type": "fatal", "message": summary}, ensure_ascii=False)
            + "\n"
        )
        wire.flush()
        return 4
    if args.preflight:
        print("[uqm-ai] all checks passed", file=sys.stderr)
        return 0

    # Fail fast and loudly on missing data: a sidecar that starts without its
    # canonical text would silently generate an out-of-character stranger.
    try:
        table = PhraseTable(StringsHeader(header), DialogueFile(dialogue))
    except Exception as exc:  # noqa: BLE001
        print(f"[uqm-ai] cannot load {args.character}: {exc}", file=sys.stderr)
        return 2

    # Fail loudly on a provider that cannot start: silently falling back to
    # the mock would look like a working AI producing very poor writing.
    provider: LLMProvider
    if args.provider == "claude":
        from .providers.claude import ClaudeProvider

        try:
            provider = ClaudeProvider()
        except ProviderError as exc:
            print(f"[uqm-ai] {exc}", file=sys.stderr)
            return 3
    else:
        provider = MockProvider()

    # Speech is optional and subtitles are not. Any failure here leaves the
    # game running with subtitles over a carrier clip, unlike a missing LLM,
    # which would leave the player typing into nothing.
    tts: TTSProvider | None = None
    if args.tts != "none":
        try:
            tts = _build_tts(args.tts, args.repo)
        except Exception as exc:  # noqa: BLE001 - never fatal
            print(f"[uqm-ai] no generated speech: {exc}", file=sys.stderr)

    Sidecar(PromptBuilder(profile, table), provider, tts=tts,
            stdout=wire).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
