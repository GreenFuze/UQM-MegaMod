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

    # Things the story must unlock. Deliberately NOT a list of everything that
    # sounds like a secret: the Syreen and the Chenjesu are Alliance races
    # Hayes fought beside, and starbas.c:407 lets him describe them from the
    # first visit with no state test at all. Gating those would be stricter
    # than the shipped game, which is its own kind of wrong.
    BLACKLIST = [
        "Sa-Matra", "Samatra", "Kohr-Ah", "Precursor bomb",
        "Taalo", "Dnyarri",
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


class TestTemplateReduction:
    """MegaMod interpolations must never reach a prompt raw.

    Their arguments are internal lookup keys, and several of them name the
    thing the phrase is hiding: getConstellation("Vulpeculae", "taalo
    protector") put "taalo protector" into a fresh-game prompt for a character
    who has never heard of the Taalo. The spoiler gate could not see it,
    because it sat inside a phrase he was allowed to speak.
    """

    def test_no_prompt_text_contains_raw_syntax(self, cast: Cast) -> None:
        for resource in cast.served:
            for entry in cast.table(resource).entries:
                assert "<%" not in (entry.text or ""), entry.key
                assert "%>" not in (entry.text or ""), entry.key

    def test_lookup_keys_do_not_survive(self, cast: Cast) -> None:
        from uqm_ai.templates import reduce_text

        raw = 'lived in the <% comm.getConstellation("Vulpeculae", "taalo protector") %> constellation'
        assert reduce_text(raw) == "lived in the Vulpeculae constellation"

    def test_player_chosen_names_become_generic(self) -> None:
        from uqm_ai.templates import reduce_text

        assert reduce_text("<% state.sis.getCaptainName() %>, report.") == (
            "the captain, report."
        )
        assert "your ship" in reduce_text("Your <% state.sis.getShipName() %> is fast.")

    def test_interpolated_entries_are_still_flagged(self, cast: Cast) -> None:
        # Under StarSeed the canonical value is wrong, so these are
        # meaning-only and their specifics must not be quoted as fact.
        table = cast.table("comm.starbase.dialogue")
        assert any(e.has_interpolation for e in table.entries)


class TestMemory:
    """Recall must survive a conversation and not survive a reload.

    The hazard is specific: a player loads a save from before a revelation, and
    the character remembers a conversation that has not happened in that
    timeline and spoils the plot to prove it.
    """

    def store(self, tmp_path: Path, limit: int = 8):
        from uqm_ai.memory import MemoryStore

        return MemoryStore(tmp_path / "memory", limit=limit)

    def test_recall_is_per_character(self, tmp_path: Path) -> None:
        store = self.store(tmp_path)
        store.remember("slot0", "comm.spathi.dialogue", date(2155, 3, 1), "He panicked.")
        assert store.recall("slot0", "comm.spathi.dialogue", date(2155, 6, 1))
        assert not store.recall("slot0", "comm.urquan.dialogue", date(2155, 6, 1))

    def test_recall_is_per_save(self, tmp_path: Path) -> None:
        # Two playthroughs must not share a memory; without a real save id
        # a character would greet a brand-new captain by name.
        store = self.store(tmp_path)
        store.remember("slot0", "x", date(2155, 3, 1), "first game")
        assert not store.recall("slot3", "x", date(2155, 6, 1))

    def test_loading_an_earlier_save_drops_the_future(self, tmp_path: Path) -> None:
        store = self.store(tmp_path)
        store.remember("slot0", "x", date(2155, 3, 1), "early")
        store.remember("slot0", "x", date(2157, 3, 1), "late")
        assert len(store.recall("slot0", "x", date(2157, 6, 1))) == 2

        # The player reloads a save from 2156. The 2157 meeting did not happen.
        assert len(store.recall("slot0", "x", date(2156, 1, 1))) == 1
        # and it is gone, not merely hidden
        assert len(store.recall("slot0", "x", date(2157, 6, 1))) == 1

    def test_recall_carries_how_long_ago(self, tmp_path: Path) -> None:
        store = self.store(tmp_path)
        store.remember("slot0", "x", date(2155, 3, 1), "he asked about the bomb")
        recalled = store.recall("slot0", "x", date(2155, 9, 1))
        assert "months ago" in recalled[0]
        assert "he asked about the bomb" in recalled[0]

    def test_the_same_beat_is_not_remembered_twice(self, tmp_path: Path) -> None:
        store = self.store(tmp_path)
        for _ in range(3):
            store.remember("slot0", "x", date(2155, 3, 1), "He panicked.")
        assert len(store) == 1

    def test_recall_is_bounded(self, tmp_path: Path) -> None:
        store = self.store(tmp_path, limit=3)
        for day in range(1, 9):
            store.remember("slot0", "x", date(2155, 3, day), f"meeting {day}")
        recalled = store.recall("slot0", "x", date(2156, 1, 1))
        assert len(recalled) == 3
        assert "meeting 8" in recalled[-1]      # oldest dropped, newest kept

    def test_empty_text_is_not_a_memory(self, tmp_path: Path) -> None:
        store = self.store(tmp_path)
        store.remember("slot0", "x", date(2155, 3, 1), "   ")
        assert len(store) == 0

    def test_memory_survives_a_restart(self, tmp_path: Path) -> None:
        first = self.store(tmp_path)
        first.remember("slot0", "x", date(2155, 3, 1), "they met at the starbase")

        second = self.store(tmp_path)      # a fresh process, same save
        assert second.recall("slot0", "x", date(2155, 6, 1))

    def test_a_corrupt_store_is_forgotten_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "memory").mkdir(parents=True)
        (tmp_path / "memory" / "slot0.json").write_text("{ not json", encoding="utf-8")
        assert self.store(tmp_path).recall("slot0", "x", date(2155, 6, 1)) == ()


class TestElapsed:
    @pytest.mark.parametrize(
        "days,expected",
        [(0, "earlier today"), (1, "yesterday"), (5, "5 days ago"),
         (21, "3 weeks ago"), (90, "about 3 months ago"), (400, "about 13 months ago"),
         (900, "over 2 years ago")],
    )
    def test_phrasing(self, days: int, expected: str) -> None:
        from datetime import timedelta

        from uqm_ai.memory import elapsed

        then = date(2155, 2, 17)
        assert elapsed(then, then + timedelta(days=days)) == expected


class TestRecap:
    """A finished conversation becomes one line, off the critical path."""

    def test_a_finished_encounter_is_remembered(self, tmp_path: Path) -> None:
        from uqm_ai.memory import MemoryStore
        from uqm_ai.providers.mock import MockProvider
        from uqm_ai.recap import Encounter, Recapper

        store = MemoryStore(tmp_path / "memory")
        encounter = Encounter(save_id="slot0", character="x")
        encounter.add("hello", "greetings")
        encounter.add("who are you", "I am nobody")

        thread = Recapper(MockProvider(), store).finish(encounter, date(2155, 3, 1))
        thread.join(timeout=5)

        recalled = store.recall("slot0", "x", date(2155, 4, 1))
        assert recalled and "2 times" in recalled[0]

    def test_an_empty_encounter_is_not_remembered(self, tmp_path: Path) -> None:
        from uqm_ai.memory import MemoryStore
        from uqm_ai.providers.mock import MockProvider
        from uqm_ai.recap import Encounter, Recapper

        store = MemoryStore(tmp_path / "memory")
        empty = Encounter(save_id="slot0", character="x")
        assert Recapper(MockProvider(), store).finish(empty, date(2155, 3, 1)) is None
        assert len(store) == 0

    def test_the_transcript_is_bounded(self) -> None:
        from uqm_ai.recap import MAX_TURNS, Encounter

        encounter = Encounter(save_id="slot0", character="x")
        for n in range(MAX_TURNS + 10):
            encounter.add(f"q{n}", f"a{n}")
        assert len(encounter.turns) == MAX_TURNS
        assert encounter.turns[-1] == (f"q{MAX_TURNS + 9}", f"a{MAX_TURNS + 9}")


class TestVoice:
    """Length and register are characterisation, so they are per character."""

    def test_every_character_states_a_reply_length(self, cast: Cast) -> None:
        # A chat-trained model left to itself writes an essay. The closing
        # instruction is the only thing standing between that and a game
        # character, so no file may be silent about it.
        for resource in cast.served:
            assert cast.profile(resource).reply_length.strip()

    def test_the_length_instruction_reaches_the_prompt(self, cast: Cast) -> None:
        builder = cast.builder(STARBASE)
        prompt = builder.render(permitted_keys=(), state={}, today=TODAY)
        assert builder.profile.reply_length in prompt
        assert "never offer them a list of options" in prompt

    def test_narration_does_not_carry_the_converse_closing(self, cast: Cast) -> None:
        # Narration rewords an outcome the encounter already applied, so a
        # length cap and "do not restate" belong nowhere near it. With them, a
        # whole briefing came back as "Human - say again?" and the content was
        # simply gone.
        builder = cast.builder(STARBASE)
        prompt = builder.render(
            permitted_keys=(), state={}, today=TODAY, narrating=True
        )
        assert builder.profile.reply_length not in prompt
        assert "do not shorten it into a reaction" in prompt
        assert "Keep every fact" in prompt

    def test_register_reaches_the_prompt(self, cast: Cast) -> None:
        builder = cast.builder(STARBASE)
        prompt = builder.render(permitted_keys=(), state={}, today=TODAY)
        assert "career officer" in prompt

    def test_brevity_is_not_uniform(self, cast: Cast) -> None:
        # Fwiffo's verbosity is the joke and the Kohr-Ah's brevity is the
        # threat. A single global cap would flatten both.
        lengths = {
            cast.profile(r).reply_length for r in cast.served
        }
        assert len(lengths) > 3

    def test_characters_who_never_swear_say_so(self, cast: Cast) -> None:
        # The register field must be explicit in both directions: silence
        # would leave the model to guess, and it guesses towards chat.
        for resource in ("comm.urquan.dialogue", "comm.kohrah.dialogue",
                         "comm.druuge.dialogue", "comm.spathi.dialogue"):
            register = cast.profile(resource).register or ""
            assert "do not swear" in register, resource


class TestPromptBudget:
    """Everything permitted is quoted verbatim, and that has to stay affordable.

    Retrieval was considered and rejected: a prompt keyed on player input
    changes every turn and defeats caching, which is worth far more than the
    tokens it would save.
    """

    def test_no_character_is_extravagant(self, cast: Cast) -> None:
        for resource in cast.served:
            builder = cast.builder(resource)
            profile = builder.profile
            wide = {flag: 9 for flag in profile.flags()}
            wide.update({
                "MELNORME_EVENTS_INFO_STACK": 8,
                "MELNORME_ALIEN_INFO_STACK": 16,
                "MELNORME_HISTORY_INFO_STACK": 9,
                "STARBASE_BULLETS": 0xFFFFFFFF,
            })
            prompt = builder.render(
                permitted_keys=builder.unlocked_keys(wide, TODAY),
                state=wide, today=TODAY,
            )
            # This is a synthetic ceiling - every flag at once, every bulletin
            # bit set - and not reachable in play, since supersedes retires
            # much of it. Measured worst case is starbase at ~40k chars,
            # about 10k tokens, which is affordable and caches. The bound is
            # here to catch a file growing into a problem, not to pin a number.
            assert len(prompt) < 60000, f"{resource} renders {len(prompt)} chars"


class TestBulletins:
    """Past news: the bit is set, the visit is over, the captain asks again."""

    ALL_NEWS = {"STARBASE_BULLETS": 0xFFFFFFFF}

    def test_nothing_is_recalled_before_it_was_reported(self, cast: Cast) -> None:
        keys = cast.builder(STARBASE).unlocked_keys({}, TODAY)
        assert not [k for k in keys if k.startswith("STARBASE_BULLETIN")]

    def test_a_delivered_bulletin_can_be_asked_about_later(self, cast: Cast) -> None:
        # bit 5 is the Arilou arriving and gifting three ships - the case where
        # a player reasonably asks "where did these ships come from?"
        keys = cast.builder(STARBASE).unlocked_keys(
            {"STARBASE_BULLETS": 1 << 5}, TODAY
        )
        assert "STARBASE_BULLETIN_6" in keys
        assert "STARBASE_BULLETIN_1" not in keys

    def test_the_druuge_verdict_supersedes_the_rumour(self, cast: Cast) -> None:
        profile = cast.profile(STARBASE)
        both = {"STARBASE_BULLETS": (1 << 26) | (1 << 29)}
        ids = {i.id for i in profile._live_knowledge(both, TODAY)}
        assert "news_slave_trader" in ids
        assert "news_druuge_rumour" not in ids

    def test_all_the_news_still_fits(self, cast: Cast) -> None:
        keys = cast.builder(STARBASE).unlocked_keys(self.ALL_NEWS, TODAY)
        bulletins = [k for k in keys if k.startswith("STARBASE_BULLETIN")]
        # 23 slots are used, but with every bit set the slave-trader verdict
        # supersedes the rumour and the evidence - he does not deliver three
        # escalating opinions of the same crime at once.
        assert len(bulletins) == 21


class TestDeviceMemory:
    """He remembers an analysis he gave, after the device has gone."""

    def test_analysis_survives_the_device_leaving(self, cast: Cast) -> None:
        builder = cast.builder(STARBASE)
        aboard = builder.unlocked_keys({"TAALO_PROTECTOR_ON_SHIP": 1}, TODAY)
        assert "ABOUT_SHIELD" in aboard

        # The captain takes it and uses it. He still analysed it personally.
        after = builder.unlocked_keys({"DISCUSSED_TAALO_PROTECTOR": 1}, TODAY)
        assert "ABOUT_SHIELD" in after

    def test_he_cannot_analyse_what_he_never_saw(self, cast: Cast) -> None:
        keys = cast.builder(STARBASE).unlocked_keys({}, TODAY)
        assert "ABOUT_SHIELD" not in keys


class TestNotificationNeverReplies:
    """encounter_end must put NOTHING on the wire that the game will read.

    The game writes it and does not read afterwards. Any reply - including an
    error - stays in the pipe and is consumed as the answer to the NEXT
    request, putting every turn after it one message out of step. A log line
    is the exception: readMessage drains those (aiconv.c:390) and keeps going.
    """

    @staticmethod
    def _run(cast: Cast, lines: list[str]) -> list[dict]:
        import io
        import json as _json

        from uqm_ai.providers.mock import MockProvider
        from uqm_ai.sidecar import Sidecar

        out = io.StringIO()
        Sidecar(
            cast, MockProvider(),
            stdin=io.StringIO("\n".join(lines) + "\n"),
            stdout=out, log=io.StringIO(),
        ).run()
        return [_json.loads(line) for line in out.getvalue().splitlines()]

    def test_a_well_formed_notification_is_silent(self, cast: Cast) -> None:
        replies = self._run(cast, [
            '{"type":"encounter_end","session_character":"comm.spathi.dialogue"}'
        ])
        assert [r for r in replies if r.get("type") != "log"] == []

    def test_a_malformed_notification_is_also_silent(self, cast: Cast) -> None:
        # No character named. This used to raise ProtocolError, fall into the
        # generic handler, and emit an error nobody would ever read.
        replies = self._run(cast, ['{"type":"encounter_end"}'])
        assert [r for r in replies if r.get("type") != "log"] == []

    def test_the_next_request_still_lines_up(self, cast: Cast) -> None:
        # The regression that matters: a bad notification followed by a real
        # turn must produce exactly one non-log reply, and it must answer the
        # turn.
        import json as _json

        turn = {
            "type": "converse", "id": 77,
            "session_character": "comm.spathi.dialogue",
            "player_input": "hello",
            "actions": [{"ref": 1, "text": "hi", "terminal": False}],
        }
        replies = self._run(cast, [
            '{"type":"encounter_end"}', _json.dumps(turn)
        ])
        answers = [r for r in replies if r.get("type") != "log"]
        assert len(answers) == 1
        assert answers[0]["id"] == 77


class TestMemoryIsThreadSafe:
    """The recap thread files a summary while a live turn may be recalling."""

    def test_concurrent_writes_and_reads_do_not_fault(self, tmp_path: Path) -> None:
        import threading

        from uqm_ai.memory import MemoryStore

        store = MemoryStore(tmp_path / "memory")
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                for n in range(200):
                    store.remember("slot0", "x", date(2155, 3, 1), f"note {n}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(200):
                    store.recall("slot0", "x", date(2155, 6, 1))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert not errors, errors
