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

A character is SERVED only if it also has an authored file in ai/characters/.
Everything resolvable is listed either way, so the sidecar can say plainly
which characters it can speak for and the game falls back to the authored
menu for the rest. That is the rollout switch: adding a file turns one
character on, and turning one on cannot affect any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .character import CharacterError, CharacterProfile, load_character
from .dialogue import DialogueFile
from .gamestate import flag_names
from .persona import PromptBuilder
from .phrase_table import PhraseTable, StringsHeader

_MARKER = "/* PlayerPhrases */"


class CastError(Exception):
    """Raised when the character map cannot be built from the trees."""


@dataclass(frozen=True)
class CharacterSpec:
    """Where one character's data files live."""

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

    Construction resolves every character and loads every authored file, so a
    malformed condition or a flag the game does not have is fatal at startup
    rather than mid-conversation. It parses no phrases: a 27-way eager load
    would read 3,262 phrases for a session that will speak to two or three of
    them. Tables and builders are made on first use and cached.
    """

    def __init__(self, repo: Path, content: Path,
            characters: Path | None = None) -> None:
        self._repo = Path(repo)
        self._content = Path(content)
        self._specs = self._resolve()

        root = Path(characters) if characters else self._repo / "ai" / "characters"
        self._profiles = self._load_profiles(root)
        self._tables: dict[str, PhraseTable] = {}
        self._builders: dict[str, PromptBuilder] = {}

    # --- what exists ----------------------------------------------------

    @property
    def specs(self) -> dict[str, CharacterSpec]:
        """Every character resolvable from the trees, authored or not."""
        return dict(self._specs)

    @property
    def served(self) -> frozenset[str]:
        """Characters with an authored file, which are the ones we can voice."""
        return frozenset(self._profiles)

    def spec(self, dialogue_res: str) -> CharacterSpec:
        try:
            return self._specs[dialogue_res]
        except KeyError:
            raise CastError(f"unknown character {dialogue_res!r}") from None

    def profile(self, dialogue_res: str) -> CharacterProfile:
        try:
            return self._profiles[dialogue_res]
        except KeyError:
            self.spec(dialogue_res)   # raises first if it is not a character at all
            raise CastError(
                f"no authored character file for {dialogue_res!r}; "
                f"the game should fall back to its own dialogue"
            ) from None

    # --- built on demand -------------------------------------------------

    def table(self, dialogue_res: str) -> PhraseTable:
        """The character's phrase table, built on first use."""
        table = self._tables.get(dialogue_res)
        if table is None:
            spec = self.spec(dialogue_res)
            aliases = dict(self._profiles[dialogue_res].aliases) \
                if dialogue_res in self._profiles else None
            table = PhraseTable(
                StringsHeader(spec.header),
                DialogueFile(spec.dialogue),
                aliases=aliases,
            )
            self._tables[dialogue_res] = table
        return table

    def builder(self, dialogue_res: str) -> PromptBuilder:
        """The character's prompt builder, built on first use."""
        builder = self._builders.get(dialogue_res)
        if builder is None:
            builder = PromptBuilder(
                self.profile(dialogue_res), self.table(dialogue_res)
            )
            self._builders[dialogue_res] = builder
        return builder

    # --- resolution -----------------------------------------------------

    def _load_profiles(self, root: Path) -> dict[str, CharacterProfile]:
        if not root.is_dir():
            raise CastError(f"no character directory at {root}")

        known = flag_names(self._repo)
        profiles: dict[str, CharacterProfile] = {}

        for path in sorted(root.glob("*.toml")):
            profile = load_character(path, known_flags=known)

            if profile.dialogue_res not in self._specs:
                raise CharacterError(
                    f"{path}: dialogue = {profile.dialogue_res!r} is not a "
                    f"character the game has"
                )
            if profile.dialogue_res in profiles:
                raise CharacterError(
                    f"{path}: {profile.dialogue_res!r} is already described by "
                    f"another file"
                )
            profiles[profile.dialogue_res] = profile

        return profiles

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
