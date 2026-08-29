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
from .cast import Cast
from .memory import MemoryStore
from .preflight import Preflight, report
from .providers.base import LLMProvider, ProviderError, TTSProvider
from .providers.mock import MockProvider
from .sidecar import Sidecar


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


def _reference_clips(repo: Path, cast: Cast) -> dict[str, Path]:
    """One reference recording per character, from the player's own content.

    Each character's own voice, derived on the player's machine from the voice
    pack they already have. Nothing cloned is committed, downloaded from us, or
    shipped; the only thing that makes the model sound like anyone is a file
    already in their content directory.

    A character with no recording is simply absent, and speaks in subtitles.
    """
    content = repo.parent / "uqm-megamod-content" / "addons" / "mm-3dovoice"
    clips: dict[str, Path] = {}

    for resource in sorted(cast.served):
        spec = cast.spec(resource)
        name = cast.profile(resource).voice_clip or f"{spec.content_key}-001.ogg"
        path = content / spec.content_key / name
        if path.is_file():
            clips[resource] = path
    return clips


def _build_tts(kind: str, repo: Path, cast: Cast) -> TTSProvider:
    clips = _reference_clips(repo, cast)
    if not clips:
        raise ProviderError("no voice references found; is the 3DO voice pack installed?")

    if kind == "canned":
        from .providers.canned_tts import CannedTTS

        return CannedTTS(clips)

    if kind == "chatterbox":
        from .providers.chatterbox_tts import ChatterboxVoice

        return ChatterboxVoice(clips)

    raise ProviderError(f"unknown tts provider {kind!r}")


def main(argv: list[str] | None = None) -> int:
    wire = _claim_wire()
    gamelog.attach(wire)

    parser = argparse.ArgumentParser(prog="uqm_ai")
    parser.add_argument(
        "--character",
        default=None,
        help="dialogue resource to pre-warm, e.g. comm.spathi.dialogue. "
             "Every authored character is served regardless; this only "
             "decides which table is built before the first request",
    )
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

    # Resolving the cast reads the trees and every character file, so a
    # malformed condition or a flag the game does not have stops the sidecar
    # here with a reason rather than mid-conversation.
    try:
        cast = Cast(args.repo, args.repo.parent / "uqm-megamod-content")
    except Exception as exc:  # noqa: BLE001
        print(f"[uqm-ai] cannot resolve the cast: {exc}", file=sys.stderr)
        wire.write(
            json.dumps({"type": "fatal", "message": str(exc)}, ensure_ascii=False)
            + "\n"
        )
        wire.flush()
        return 2

    # Checked before anything else: each of these has previously failed in a
    # way that looked like the game hanging rather than like a missing
    # prerequisite.
    problems = Preflight(args.repo, cast).run(
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
        print(
            f"[uqm-ai] all checks passed; serving {len(cast.served)} of "
            f"{len(cast.specs)} characters",
            file=sys.stderr,
        )
        return 0

    if args.character:
        # Pre-warm one table so the first turn does not pay for parsing it.
        try:
            cast.builder(args.character)
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
            tts = _build_tts(args.tts, args.repo, cast)
        except Exception as exc:  # noqa: BLE001 - never fatal
            print(f"[uqm-ai] no generated speech: {exc}", file=sys.stderr)

    gamelog.emit(
        f"serving {len(cast.served)} of {len(cast.specs)} characters: "
        + ", ".join(sorted(cast.served))
    )
    # Memory persists between launches, keyed on the save the game names.
    # Entries carry the in-game date they were written on, so loading an
    # earlier save discards anything dated after it - see memory.py.
    memory = MemoryStore(args.repo / "ai" / "data" / "memory")
    Sidecar(cast, provider, tts=tts, stdout=wire, memory=memory).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
