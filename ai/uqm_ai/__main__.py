"""Entry point: python -m uqm_ai --character fwiffo

Wires a character's phrase table to a provider and serves stdio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dialogue import DialogueFile
from .persona import FWIFFO, PromptBuilder
from .phrase_table import PhraseTable, StringsHeader
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


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    profile, header_rel, dialogue_rel = _CHARACTERS[args.character]

    # Fail fast and loudly on missing data: a sidecar that starts without its
    # canonical text would silently generate an out-of-character stranger.
    try:
        table = PhraseTable(
            StringsHeader(args.repo / header_rel),
            DialogueFile(args.repo / dialogue_rel),
        )
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
