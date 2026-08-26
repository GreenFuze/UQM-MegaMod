"""Entry point: python -m uqm_ai --character fwiffo

Wires a character's phrase table to a provider and serves stdio.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dialogue import DialogueFile
from .persona import FWIFFO, PromptBuilder
from .phrase_table import PhraseTable, StringsHeader
from .preflight import Preflight, report
from .providers.base import LLMProvider, ProviderError
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


def _use_utf8_wire() -> None:
    """Pin stdio to UTF-8 before a single byte is written.

    Python picks the console code page on Windows - cp1252 here - so any
    non-ASCII character in a reply was silently transcoded into a byte the
    game could not render, and anything the code page cannot represent at all
    would raise mid-write and take the turn with it. The protocol is UTF-8;
    say so rather than inheriting whatever the console happens to be.
    """
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    _use_utf8_wire()

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
        print(
            json.dumps({"type": "fatal", "message": summary}, ensure_ascii=False),
            flush=True,
        )
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

    Sidecar(PromptBuilder(profile, table), provider).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
