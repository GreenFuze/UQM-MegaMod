"""Every character the game can hand us, resolved from the trees themselves.

The game identifies a character on the wire by its dialogue resource name -
"comm.starbase.dialogue" - because that string is already sitting in
LOCDATA.ConversationPhrasesRes at runtime and is already correct across the
forks init_race performs (commander/starbase on STARBASE_AVAILABLE,
spathi/safeones on the homeworld bit). Nothing per-race had to be added to the
game to obtain it.

Turning that name into the two files we need is a join across three sources,
none of which we maintain:

    src/uqm/comm/<dir>/*.c   the line marked  /* PlayerPhrases */  names a macro
    src/uqm/comm/*/resinst.h that macro expands to the resource name
    uqm.rmp                  the resource name maps to the content .txt

Deriving it matters rather than being tidy. Twelve content directories are
named differently from their source directory, and probe/slylandro are
SWAPPED: source slyland is the probe, source slyhome is the homeworld. A
hand-written map is one transcription error away from attributing a whole
character's words to the wrong species, which is exactly the failure
PhraseTable exists to catch.

The scan also excludes robot/ for free: it has no conversation .c because it
is a phoneme table for the starbase computer, not a character.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dialogue import DialogueFile
from .phrase_table import PhraseTable, StringsHeader

# Positional key renames: the enum and the dialogue file disagree on a name at
# a position where they agree on everything else. MegaMod's artifact
# randomisation renames these two in the content but not in the enum. Position
# is what the game dispatches on, so an explicit alias is safe where a blanket
# relaxation would not be. Moves into the per-character data file when that
# exists.
_ALIASES: dict[str, dict[int, str]] = {
    "comm.starbase.dialogue": {
        151: "ABOUT_ARTIFACT_2",   # enum says ABOUT_WIMBLIS_TRIDENT
        152: "ABOUT_ARTIFACT_3",   # enum says ABOUT_GLOWING_ROD
    },
}

_MARKER = "/* PlayerPhrases */"


class CastError(Exception):
    """Raised when the character map cannot be built from the trees."""


@dataclass(frozen=True)
class CharacterSpec:
    """Where one character's two data files live."""

    dialogue_res: str
    source_dir: str
    header: Path
    dialogue: Path

    @property
    def content_key(self) -> str:
        """The content directory name, e.g. 'safeones'. Also the voice dir."""
        return self.dialogue.parent.name


class Cast:
    """The set of characters the sidecar can serve.

    Construction resolves every character but parses no phrases: a 27-way
    eager load would read 3,262 phrases at startup for a session that will
    speak to two or three of them. Tables are built on first use and cached.
    """

    def __init__(self, repo: Path, content: Path) -> None:
        self._repo = Path(repo)
        self._content = Path(content)
        self._specs = self._resolve()
        self._tables: dict[str, PhraseTable] = {}

    @property
    def specs(self) -> dict[str, CharacterSpec]:
        return dict(self._specs)

    def spec(self, dialogue_res: str) -> CharacterSpec:
        try:
            return self._specs[dialogue_res]
        except KeyError:
            raise CastError(f"unknown character {dialogue_res!r}") from None

    def table(self, dialogue_res: str) -> PhraseTable:
        """The character's phrase table, built on first use."""
        table = self._tables.get(dialogue_res)
        if table is None:
            spec = self.spec(dialogue_res)
            table = PhraseTable(
                StringsHeader(spec.header),
                DialogueFile(spec.dialogue),
                aliases=_ALIASES.get(dialogue_res),
            )
            self._tables[dialogue_res] = table
        return table

    # --- resolution -----------------------------------------------------

    def _resolve(self) -> dict[str, CharacterSpec]:
        macros = self._macro_definitions()
        resources = self._resource_paths()

        specs: dict[str, CharacterSpec] = {}
        comm = self._repo / "src" / "uqm" / "comm"
        if not comm.is_dir():
            raise CastError(f"no conversation source at {comm}")

        for source in sorted(p for p in comm.iterdir() if p.is_dir()):
            macro = self._phrases_macro(source)
            if macro is None:
                continue          # robot/ and anything else that is not a character

            resource = macros.get(macro)
            if resource is None:
                raise CastError(
                    f"{source.name}: macro {macro} is not defined by any resinst.h"
                )
            relative = resources.get(resource)
            if relative is None:
                raise CastError(
                    f"{source.name}: resource {resource} is not in uqm.rmp"
                )

            specs[resource] = CharacterSpec(
                dialogue_res=resource,
                source_dir=source.name,
                header=source / "strings.h",
                dialogue=self._content / relative,
            )
        return specs

    @staticmethod
    def _phrases_macro(source: Path) -> str | None:
        """The macro named on the LOCDATA line marked /* PlayerPhrases */."""
        for path in sorted(source.glob("*.c")):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if _MARKER in line:
                    return line.split(",")[0].strip()
        return None

    def _macro_definitions(self) -> dict[str, str]:
        """Every string #define across all resinst.h files, as one namespace.

        A union rather than per-directory, because a character can use a macro
        its neighbour defines: spahome's dialogue resource is declared in
        spathi/resinst.h.
        """
        macros: dict[str, str] = {}
        for header in sorted(self._repo.glob("src/uqm/comm/*/resinst.h")):
            for line in header.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] == "#define" and parts[2][:1] == '"':
                    macros[parts[1]] = parts[2].strip('"')
        return macros

    def _resource_paths(self) -> dict[str, str]:
        """Conversation resources from uqm.rmp, as resource name to path."""
        rmp = self._content / "uqm.rmp"
        if not rmp.is_file():
            raise CastError(f"resource map not found: {rmp}")

        paths: dict[str, str] = {}
        for line in rmp.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or "CONVERSATION:" not in line:
                continue
            name, value = line.split("=", 1)
            paths[name.strip()] = value.split("CONVERSATION:", 1)[1].strip()
        return paths
