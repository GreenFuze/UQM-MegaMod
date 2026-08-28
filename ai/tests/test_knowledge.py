"""What a character may know, and when.

The load-bearing test here is TestSpoilerGate: it asserts against the actual
rendered prompt, not against the model's output, so it is deterministic and
runs in milliseconds. It is the executable form of the invariant - the base
model already knows Star Control II, so the only real defence is that the
prompt never contains the thing.
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from uqm_ai.cast import Cast
from uqm_ai.character import CharacterError, load_character
from uqm_ai.gamestate import DERIVED_VALUES, flag_names
from uqm_ai.precond import PreconditionError, parse

REPO = Path(__file__).resolve().parents[2]
CONTENT = REPO.parent / "uqm-megamod-content"
CHARACTERS = REPO / "ai" / "characters"

STARBASE = "comm.starbase.dialogue"
TODAY = date(2157, 5, 14)


@pytest.fixture(scope="module")
def cast() -> Cast:
    return Cast(REPO, CONTENT)


class TestConditions:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("always", True),
            ("true", True),
            ("AWARE_OF_SAMATRA", True),
            ("KOHR_AH_FRENZY", False),
            ("not KOHR_AH_FRENZY", True),
            ("CHMMR_BOMB_STATE == 2", True),
            ("CHMMR_BOMB_STATE >= 3", False),
            ("CHMMR_BOMB_STATE < 3", True),
            ("date >= 2157-01-01", True),
            ("date >= 2159-01-01", False),
            ("AWARE_OF_SAMATRA and CHMMR_BOMB_STATE >= 1", True),
            ("KOHR_AH_FRENZY or AWARE_OF_SAMATRA", True),
            ("(KOHR_AH_FRENZY or AWARE_OF_SAMATRA) and date >= 2156-01-01", True),
        ],
    )
    def test_evaluation(self, text: str, expected: bool) -> None:
        state = {"CHMMR_BOMB_STATE": 2, "AWARE_OF_SAMATRA": 1}
        assert parse(text).evaluate(state, TODAY) is expected

    def test_absent_flag_is_zero_not_an_error(self) -> None:
        # getGameStateUint returns 0 for an unset property (lua/luastate.c),
        # and a knowledge item guarded by a flag the game did not send this
        # turn must stay shut rather than kill the turn.
        assert parse("NEVER_SENT").evaluate({}, TODAY) is False
        assert parse("NEVER_SENT == 0").evaluate({}, TODAY) is True

    def test_a_list_is_a_conjunction(self) -> None:
        node = parse(["AWARE_OF_SAMATRA", "date >= 2156-01-01"])
        assert node.evaluate({"AWARE_OF_SAMATRA": 1}, TODAY) is True
        assert node.evaluate({}, TODAY) is False

    @pytest.mark.parametrize(
        "text",
        ["", "FLAG >=", "FLAG >= two", "date >= soon", "(FLAG", "FLAG FLAG2",
         "1 + 1", "a and b or c"],
    )
    def test_malformed_is_refused(self, text: str) -> None:
        with pytest.raises(PreconditionError):
            parse(text)

    def test_flags_are_reported(self) -> None:
        node = parse("AWARE_OF_SAMATRA and CHMMR_BOMB_STATE >= 1")
        assert node.flags() == {"AWARE_OF_SAMATRA", "CHMMR_BOMB_STATE"}


class TestCharacterFiles:
    def test_every_shipped_file_loads(self, cast: Cast) -> None:
        assert cast.served, "no characters are authored"
        for resource in cast.served:
            assert cast.profile(resource).description.strip()

    def test_every_condition_names_a_real_flag(self, cast: Cast) -> None:
        # A flag the game does not have reads as 0 forever, so the item never
        # unlocks. That failure is invisible in play and looks exactly like
        # "we have not reached that part of the story yet".
        known = flag_names(REPO)
        for resource in cast.served:
            unknown = sorted(cast.profile(resource).flags() - known)
            assert not unknown, f"{resource} names unknown flags: {unknown}"

    def test_every_phrase_named_exists_in_the_table(self, cast: Cast) -> None:
        for resource in cast.served:
            keys = {e.key for e in cast.table(resource).entries}
            for item in cast.profile(resource).knowledge:
                missing = sorted(set(item.phrases) - keys)
                assert not missing, f"{resource}/{item.id} names missing: {missing}"

    def test_derived_values_are_documented(self) -> None:
        # They are not in gameStateBitMap, so if one is added to a character
        # file without being computed in aistate.c it silently reads zero.
        assert DERIVED_VALUES
        assert all(name.startswith("SIS_") for name in DERIVED_VALUES)

    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "x.toml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_unknown_key_is_refused(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, """
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            colour = "purple"
        """)
        with pytest.raises(CharacterError, match="unrecognised"):
            load_character(path)

    def test_lore_without_a_source_is_refused(self, tmp_path: Path) -> None:
        # Without provenance the model invents a chain of custody, and the
        # invented chain is itself a spoiler.
        path = self._write(tmp_path, """
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            [[lore]]
            id = "a"
            text = "something true"
        """)
        with pytest.raises(CharacterError, match="source"):
            load_character(path)

    def test_duplicate_ids_are_refused(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, """
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            [[knowledge]]
            id = "a"
            fact = "one"
            [[knowledge]]
            id = "a"
            fact = "two"
        """)
        with pytest.raises(CharacterError, match="duplicate"):
            load_character(path)

    def test_unknown_flag_is_refused_when_checked(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, """
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            [[knowledge]]
            id = "a"
            when = "NO_SUCH_FLAG_AT_ALL"
            fact = "one"
        """)
        with pytest.raises(CharacterError, match="does not have"):
            load_character(path, known_flags=flag_names(REPO))

    def test_superseding_an_unknown_id_is_refused(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, """
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            [[knowledge]]
            id = "a"
            fact = "one"
            supersedes = ["nope"]
        """)
        with pytest.raises(CharacterError, match="supersedes"):
            load_character(path)


class TestStoryProgression:
    """The Commander, at four points, must answer the same question differently."""

    FRESH: dict[str, int] = {}
    ALLIED = {"SIS_ALLY_COUNT": 3}
    AWARE = {"SIS_ALLY_COUNT": 3, "AWARE_OF_SAMATRA": 1}
    ARMED = {
        "SIS_ALLY_COUNT": 5, "AWARE_OF_SAMATRA": 1, "UTWIG_BOMB": 1,
        "CHMMR_BOMB_STATE": 2, "TALKING_PET_ON_SHIP": 1,
    }

    def keys(self, cast: Cast, state: dict[str, int]) -> tuple[str, ...]:
        return cast.builder(STARBASE).unlocked_keys(state, TODAY)

    def test_fresh_game_does_not_know_the_samatra(self, cast: Cast) -> None:
        keys = self.keys(cast, self.FRESH)
        assert "GO_LEARN_ABOUT_URQUAN" in keys
        assert "KNOW_ABOUT_SAMATRA" not in keys
        assert "GO_DESTROY_SAMATRA" not in keys

    def test_allies_supersede_the_advice_to_find_them(self, cast: Cast) -> None:
        assert "GO_ALLY_WITH_ALIENS" in self.keys(cast, self.FRESH)
        allied = self.keys(cast, self.ALLIED)
        assert "MADE_SOME_ALLIES" in allied
        assert "GO_ALLY_WITH_ALIENS" not in allied

    def test_learning_of_the_samatra_replaces_the_search_for_a_weakness(
        self, cast: Cast
    ) -> None:
        aware = self.keys(cast, self.AWARE)
        assert "KNOW_ABOUT_SAMATRA" in aware
        assert "GO_LEARN_ABOUT_URQUAN" not in aware

    def test_the_bomb_retires_the_search_for_a_weapon(self, cast: Cast) -> None:
        armed = self.keys(cast, self.ARMED)
        assert "GO_DESTROY_SAMATRA" in armed
        assert "FIND_WAY_TO_DESTROY_SAMATRA" not in armed

    def test_the_denial_lifts_exactly_when_he_learns(self, cast: Cast) -> None:
        profile = cast.profile(STARBASE)
        topics = lambda st: {d.topic for d in profile.active_denials(st, TODAY)}
        superweapon = "where the Ur-Quan keep their superweapon, or what it is"
        assert superweapon in topics(self.FRESH)
        assert superweapon not in topics(self.AWARE)

    def test_earth_is_never_something_he_can_report_on(self, cast: Cast) -> None:
        # The one denial with no `unless`: the shield blocks everything, at
        # every point in the story.
        profile = cast.profile(STARBASE)
        for state in (self.FRESH, self.ALLIED, self.AWARE, self.ARMED):
            topics = {d.topic for d in profile.active_denials(state, TODAY)}
            assert any("Earth" in t for t in topics)


class TestSpoilerGate:
    """The prompt itself, not the model's output. Deterministic and cheap."""

    BLACKLIST = [
        "Sa-Matra", "Samatra", "Kohr-Ah", "Precursor bomb", "Chmmr",
        "Taalo", "Syreen", "Dnyarri", "Androsynth rebell",
    ]

    def prompt(self, cast: Cast, state: dict[str, int], when: date) -> str:
        builder = cast.builder(STARBASE)
        return builder.render(
            permitted_keys=builder.unlocked_keys(state, when),
            state=state,
            today=when,
        )

    def test_a_fresh_game_prompt_contains_no_spoiler(self, cast: Cast) -> None:
        text = self.prompt(cast, {}, date(2155, 2, 17)).lower()
        leaked = [w for w in self.BLACKLIST if w.lower() in text]
        assert not leaked, f"fresh-game prompt leaks {leaked}"

    def test_the_late_game_prompt_does_contain_it(self, cast: Cast) -> None:
        # The gate is about timing, not permanent redaction: once the story
        # unlocks a secret the character must be able to speak of it.
        text = self.prompt(cast, TestStoryProgression.ARMED, TODAY).lower()
        assert "sa-matra" in text

    def test_the_date_is_stated_and_bounded(self, cast: Cast) -> None:
        text = self.prompt(cast, {}, date(2155, 2, 17))
        assert "17 February 2155" in text
        assert "nothing of what happens after today" in text

    def test_no_wordless_phrase_reaches_the_prompt(self, cast: Cast) -> None:
        for resource in cast.served:
            builder = cast.builder(resource)
            every = tuple(e.key for e in cast.table(resource).entries)
            assert all(line.text for line in builder.canonical_lines(every))


class TestTimeline:
    """Truth and access are separate axes; both must gate."""

    def _profile(self, tmp_path: Path, when: str, extra: str = ""):
        path = tmp_path / "t.toml"
        path.write_text(textwrap.dedent(f"""
            dialogue = "comm.spathi.dialogue"
            name = "X"
            species = "Y"
            persona = "Z"
            [[lore]]
            id = "later"
            when = "{when}"
            source = "you were there"
            text = "a thing that becomes true later"
            {extra}
        """), encoding="utf-8")
        return load_character(path)

    def test_true_from_hides_the_fact_before_its_date(self, tmp_path: Path) -> None:
        profile = self._profile(tmp_path, "always", 'true_from = "2159-01-01"')
        assert not profile.permitted_lore({}, date(2155, 2, 17))
        assert profile.permitted_lore({}, date(2159, 6, 1))

    def test_true_until_retires_the_fact(self, tmp_path: Path) -> None:
        profile = self._profile(tmp_path, "always", 'true_until = "2156-01-01"')
        assert profile.permitted_lore({}, date(2155, 6, 1))
        assert not profile.permitted_lore({}, date(2157, 6, 1))

    def test_access_still_gates_a_fact_that_is_already_true(
        self, tmp_path: Path
    ) -> None:
        # It is 2159 and the fact is true, but this character has no way to
        # know it. Date alone would make him omniscient.
        profile = self._profile(
            tmp_path, "KOHR_AH_FRENZY", 'true_from = "2159-01-01"'
        )
        assert not profile.permitted_lore({}, date(2159, 6, 1))
        assert profile.permitted_lore({"KOHR_AH_FRENZY": 1}, date(2159, 6, 1))
