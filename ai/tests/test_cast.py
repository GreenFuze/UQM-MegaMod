"""Every character resolves, and every phrase table is exactly the right size.

This is the regression guard for the join between three files we do not
maintain - the comm source, the resource headers, and the content package.
A drift in any of them shifts a phrase index, and a shifted index attributes
one character's words to another character's action. The counts below were
measured against the shipped 0.8.5 content; they are not aspirations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqm_ai.cast import Cast, CastError
from uqm_ai.dialogue import PhraseKind
from uqm_ai.phrase_table import PhraseTableError, StringsHeader

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO.parent / "uqm-megamod-content"

# dialogue resource -> (source dir, phrase count). Measured, not estimated.
EXPECTED = {
    "comm.arilou.dialogue": ("arilou", 97),
    "comm.chmmr.dialogue": ("chmmr", 78),
    "comm.commander.dialogue": ("comandr", 94),
    "comm.druuge.dialogue": ("druuge", 105),
    "comm.ilwrath.dialogue": ("ilwrath", 109),
    "comm.kohrah.dialogue": ("blackur", 76),
    "comm.melnorme.dialogue": ("melnorm", 281),
    "comm.mycon.dialogue": ("mycon", 109),
    "comm.orz.dialogue": ("orz", 114),
    "comm.pkunk.dialogue": ("pkunk", 180),
    "comm.probe.dialogue": ("slyland", 86),
    "comm.safeones.dialogue": ("spahome", 143),
    "comm.shofixti.dialogue": ("shofixt", 91),
    "comm.slylandro.dialogue": ("slyhome", 114),
    "comm.spathi.dialogue": ("spathi", 135),
    "comm.starbase.dialogue": ("starbas", 267),
    "comm.supox.dialogue": ("supox", 93),
    "comm.syreen.dialogue": ("syreen", 127),
    "comm.talkingpet.dialogue": ("talkpet", 112),
    "comm.thraddash.dialogue": ("thradd", 152),
    "comm.umgah.dialogue": ("umgah", 86),
    "comm.urquan.dialogue": ("urquan", 76),
    "comm.utwig.dialogue": ("utwig", 114),
    "comm.vux.dialogue": ("vux", 102),
    "comm.yehat.dialogue": ("yehat", 68),
    "comm.yehat.rebel.dialogue": ("rebel", 34),
    "comm.zoqfotpik.dialogue": ("zoqfot", 334),
}


@pytest.fixture(scope="module")
def cast() -> Cast:
    return Cast(REPO, CONTENT)


class TestResolution:
    def test_every_character_resolves(self, cast: Cast) -> None:
        assert set(cast.specs) == set(EXPECTED)

    def test_robot_is_not_a_character(self, cast: Cast) -> None:
        # robot/ is a phoneme table for the starbase computer, not dialogue.
        # It is excluded by having no conversation .c, not by a special case.
        assert not any(s.source_dir == "robot" for s in cast.specs.values())

    @pytest.mark.parametrize("resource", sorted(EXPECTED))
    def test_source_directory(self, cast: Cast, resource: str) -> None:
        assert cast.spec(resource).source_dir == EXPECTED[resource][0]

    def test_probe_and_homeworld_are_not_swapped(self, cast: Cast) -> None:
        # The one pairing a hand-written map gets wrong: source slyland is the
        # probe, source slyhome is the homeworld - the opposite of the names.
        assert cast.spec("comm.probe.dialogue").source_dir == "slyland"
        assert cast.spec("comm.slylandro.dialogue").source_dir == "slyhome"

    def test_unknown_character_is_refused(self, cast: Cast) -> None:
        with pytest.raises(CastError):
            cast.spec("comm.nosuch.dialogue")


class TestTables:
    @pytest.mark.parametrize("resource", sorted(EXPECTED))
    def test_phrase_count(self, cast: Cast, resource: str) -> None:
        assert len(cast.table(resource)) == EXPECTED[resource][1]

    @pytest.mark.parametrize("resource", sorted(EXPECTED))
    def test_refs_are_dense_and_one_based(self, cast: Cast, resource: str) -> None:
        # Enum value R resolves to dialogue entry R-1, so any gap here is a
        # misalignment that would mis-speak a line.
        refs = [e.enum_value for e in cast.table(resource).entries]
        assert refs == list(range(1, len(refs) + 1))

    def test_tables_are_cached(self, cast: Cast) -> None:
        assert cast.table("comm.spathi.dialogue") is cast.table("comm.spathi.dialogue")


class TestKnownDefects:
    """The three races that could not load, each for a different reason."""

    def test_pkunk_skips_disabled_enum_members(self, cast: Cast) -> None:
        # pkunk/strings.h wraps NOT_CONQUER_10..12 in #if 0. The compiler never
        # emits them; counting them shifted every later phrase by three.
        names = StringsHeader(cast.spec("comm.pkunk.dialogue").header).names
        for dead in ("NOT_CONQUER_10", "NOT_CONQUER_11", "NOT_CONQUER_12"):
            assert dead not in names
        assert "NOT_CONQUER_1" in names

    def test_umgah_tolerates_a_missing_trailing_line(self, cast: Cast) -> None:
        # umgah declares OUT_TAKES and umgahc.c speaks it, but umgah.txt has no
        # entry - the stock game reads off the end of its own table.
        table = cast.table("comm.umgah.dialogue")
        assert table.by_key("OUT_TAKES").text is None
        assert sum(1 for e in table.entries if e.text is None) == 1

    def test_starbase_artifact_renames_are_aliased(self, cast: Cast) -> None:
        # MegaMod's artifact randomisation renames these in the content but not
        # in the enum. The enum name stays authoritative because that is what
        # the game dispatches on.
        table = cast.table("comm.starbase.dialogue")
        assert table.entries[151].key == "ABOUT_WIMBLIS_TRIDENT"
        assert table.entries[152].key == "ABOUT_GLOWING_ROD"
        assert table.entries[151].text


class TestAlignmentStillFails:
    """The relaxations must not have disarmed the check."""

    def test_a_longer_dialogue_file_is_fatal(self, cast: Cast) -> None:
        # An insertion shifts every later index, so a dialogue file longer than
        # the enum must stay fatal even though a shorter one is tolerated.
        from uqm_ai.dialogue import DialogueFile
        from uqm_ai.phrase_table import PhraseTable

        spathi = cast.spec("comm.spathi.dialogue")
        header = StringsHeader(spathi.header)

        class Truncated(StringsHeader):
            def __init__(self, names):
                self._names = names

        with pytest.raises(PhraseTableError, match="longer than"):
            PhraseTable(Truncated(header.names[:20]), DialogueFile(spathi.dialogue))

    def test_a_renamed_key_without_an_alias_is_fatal(self, cast: Cast) -> None:
        from uqm_ai.dialogue import DialogueFile
        from uqm_ai.phrase_table import PhraseTable

        starbase = cast.spec("comm.starbase.dialogue")
        with pytest.raises(PhraseTableError, match="misaligned"):
            PhraseTable(
                StringsHeader(starbase.header),
                DialogueFile(starbase.dialogue),
                aliases=None,
            )


class TestNpcTextIsUsable:
    """Wordless NPC phrases exist on purpose, and must never reach a prompt.

    Five NPC phrases across four races carry no words: mycon's AMBUSH_TAIL and
    RAMBLE_TAIL, talkingpet's HYPNO_TAIL, thraddash's NAME_TAIL, and starbase's
    BLANK - which even ships a null.ogg. They are real, deliberately silent
    phrases the game speaks to terminate a spliced sequence. umgah's OUT_TAKES
    is wordless for a different reason: the dialogue file simply lacks it.

    Either way the requirement is the same. A wordless phrase quoted into a
    prompt becomes an empty pair of quotation marks presented to the model as
    something the character said.
    """

    @pytest.mark.parametrize("resource", sorted(EXPECTED))
    def test_no_wordless_line_can_reach_a_prompt(
        self, cast: Cast, resource: str
    ) -> None:
        from uqm_ai.persona import CharacterProfile, PromptBuilder

        table = cast.table(resource)
        builder = PromptBuilder(
            CharacterProfile(key="t", name="T", species="T", description="T"),
            table,
        )
        every_key = tuple(e.key for e in table.entries)
        assert all(line.text for line in builder.canonical_lines(every_key))

    @pytest.mark.parametrize("resource", sorted(EXPECTED))
    def test_wordless_phrases_still_resolve_as_refs(
        self, cast: Cast, resource: str
    ) -> None:
        # The game can dispatch them, so a ref must never come back unknown.
        table = cast.table(resource)
        for entry in table.entries:
            if entry.kind is PhraseKind.NPC and not entry.text:
                assert table.by_key(entry.key).enum_value == entry.enum_value
