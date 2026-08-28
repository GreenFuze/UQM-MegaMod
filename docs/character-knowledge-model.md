# What a character knows, and when

The AI Edition replaces a menu with free text. A menu could hide a topic behind
a branch; free text cannot, because the player can ask anything at any time. So
every character needs an explicit answer to *what do you know right now*, and
that answer has to change as the story moves.

This document is the reference for that: how the shipped game already encodes
it, what our model adds, and — in [§6](#6-auditing-a-prompt) — how to work out
why a character said something it should not have.

Companion: [conversation-corpus.md](conversation-corpus.md) for what exists and
what it is called.

---

## 1. The game already knows. Read it, do not invent it.

Three facts make the extraction possible, and all three were verified:

**Flag names exist at runtime.** `globdata.h:1103` defines
`GET_GAME_STATE(SName)` as `getGameStateUint(#SName)` — it *stringifies* the
name, and the value is fetched from a Lua table keyed by that string
(`lua/luastate.c:169`). Unlike `RESPONSE_REF`, which is a bare integer by the
time the game runs, state flags can be named on the wire. Unset properties read
as 0 (`lua/luastate.c:180-184`), which is a contract worth relying on.

**The whole set is enumerable.** `gameStateBitMap[]` in `save.c:58-790` lists
**453 named flags** with their bit widths, and `serialiseGameState`
(`globdata.c:192`) already walks it calling `getGameStateUint(bmPtr->name)`.
Collecting the live state is the same loop the save file uses.

> `globdata.h:233-236` says the enum there "is now only used for the symbolic
> names, and the comments." `save.c:58` is the authoritative layout; `globdata.h`
> is the authoritative documentation, and its comments are the single most
> useful artefact in the tree for this work — see [§3](#3-flags-worth-knowing).

**Each character's surface is small.** 363 distinct flags are read across all
conversation code, but **307 are read by exactly one character**. Only 56 are
shared. So per-encounter we send that character's flags, not all 453: `starbas`
reads 73, the median is about 12, `spathi` reads 8.

That last figure is the spine of the design. It is why the wire stays small and
why authoring is finite.

---

## 2. The five idioms the game uses

There is no shared phase abstraction — every race hand-rolls its dispatch in
`Intro()`. But the same five patterns recur, and knowing them tells you where a
character's knowledge actually lives.

**Greeting ladders with a sticky top.** The universal shape. `*_VISITS`
counters (48 of them) index a `switch`, and the last case does `--NumVisits` so
the final line repeats forever:

```c
switch (NumVisits++)
{
    case 0: NPCPhrase (FRIENDLY_HOMEWORLD_HELLO_1); break;
    ...
    case 7: NPCPhrase (FRIENDLY_HOMEWORLD_HELLO_8); --NumVisits; break;
}
SET_GAME_STATE (PKUNK_HOME_VISITS, NumVisits);
```

**Consumable ordered decks.** A cursor over an array of phrase ids. The
Melnorme sell information this way — `MELNORME_EVENTS_INFO_STACK`,
`MELNORME_ALIEN_INFO_STACK`, `MELNORME_HISTORY_INFO_STACK`
(`melnorm.c:997/1015/1062`) index `ok_buy_*_lines[]`, and once a cursor reaches
the end the menu option itself disappears. This is a ready-made
progressive-disclosure ordering: for the Melnorme, "what do you know" is three
integer comparisons rather than 35 flag expressions.

Buying information also **mutates other characters' gates** — purchasing
`OK_BUY_ALIEN_RACE_14` sets `KNOW_SPATHI_PASSWORD`, `_15` sets
`KNOW_ABOUT_SHATTERED`. The Melnorme are the game's designated mechanism for
injecting knowledge across the cast.

**Topic bitmasks.** A bit per fact, so order does not matter.
`zoqfotc.c:615-712` is the best example and shows all three axes at once — a
persistent mask, a clock condition, and a within-encounter `DISABLE_PHRASE` on
the same question. It also encodes a behaviour worth copying: when
`KOHR_AH_FRENZY` fires it sets `KnowMask = KNOW_ALL`, because once you know the
Kohr-Ah are exterminating everyone the earlier war bulletins are moot.
**Learning a big thing retires smaller things.**

**Relationship dials.** Six `*_MANNER` flags — `ARILOU`, `DRUUGE`, `ORZ`,
`PKUNK`, `SPATHI`, `THRADD` — each the *first* branch of its race's `Intro`,
each 2 bits. Typically 0 unmet / 1 hostile but salvageable / 2 hostile forever
/ 3 friendly. They are written on exit, in `post_*_enc`, not in the tree.

Crucially, **crossing a tier resets the greeting counters**
(`pkunkc.c:1088-1092`): each relationship tier gets a fresh ladder. So "what has
been said" is not monotonic — it is scoped to the current relationship.

**A dedicated already-told-you register.** `STARBASE_BULLETS` is 32 bits
(`starbas.c:1463-1690`), one per news item, set once the item has fired and been
reported; passing `Repeat` inverts the mask so the player can re-hear old
bulletins. Alongside it, the 21 `DISCUSSED_*` flags pair with `*_ON_SHIP` to
record "have I briefed him on this object yet". **This is the most explicit
knowledge bookkeeping in the game and it exists for exactly one character** —
which is why the Starbase Commander is the right pilot.

---

## 3. Flags worth knowing

The 56 shared flags are the game's common world model. The top of that list:

| flag | characters | what it means |
|---|--:|---|
| `GLOBAL_FLAGS_AND_DATA` | 13 | **location, not knowledge.** Bit 7 is "at the homeworld" and picks which file even loads. Cleared when the conversation ends. `globdata.h:442-453` admits the design is opaque |
| `KOHR_AH_FRENZY` | 6 | the endgame switch — the Kohr-Ah won the Doctrinal Conflict and are exterminating everyone. Rewrites the top-level greeting for six unrelated races |
| `KNOW_ABOUT_SHATTERED` | 5 | 0 unknown / 1 seen a shattered world / 2 knows Mycon Deep Children cause it / 3 has told the Syreen |
| `TALKING_PET_ON_SHIP` | 5 | carrying the Dnyarri; the Ur-Quan notice |
| `CHMMR_BOMB_STATE` | 4 | 0 nothing known / 1 knows a superweapon is needed / 2 installation started / 3 left the starbase after installation |
| `FOUND_PLUTO_SPATHI` | 4 | met Fwiffo |
| `ULTRON_CONDITION` | 3 | 0 Supox have it … 5 returned to the Utwig |
| `AWARE_OF_SAMATRA` | 2 | knows the Sa-Matra exists |

`MELNORME_ANGER` is read by one character but is the highest-leverage single
flag in the codebase: 2 bits selecting four entirely disjoint conversation trees
(`melnorm.c:2026-2063`).

Read the comments in `globdata.h:237-980` before authoring any character. They
document the semantics of the multi-value flags, and they are the difference
between `CHMMR_BOMB_STATE >= 1` meaning the right thing and meaning nothing.

---

## 4. Time is a separate axis

The game has a real calendar — `GLOBAL(GameClock.day_index/month_index/year_index)`
(`clock.h:40-41`), starting February 2155 — and it **already gates knowledge on
it**. `pkunkc.c:892-899` fires `SENSE_KOHRAH_VICTORY` on
`year_index > START_YEAR`; `starbas.c:1456-1460` is a full date comparison;
`zoqfotc.c` computes war news from `START_YEAR + YEARS_TO_KOHRAH_VICTORY`, whose
value varies with difficulty (`DIF_CASE`).

So the timeline is not something we impose. It is something we must not break,
and it is **orthogonal to state**:

- **Diegetic truth** — is this true in-universe right now? → a date condition.
- **Epistemic access** — does *this character*, here, have any way to know it? →
  a state condition.

Both are required, and conflating them fails in two different directions:

*State only.* The player picks up an unrelated flag in March 2155 and the
Commander says the Kohr-Ah have begun the Death March — an event that resolves
around 2159. He has narrated a future.

*Date only.* It is 2159, the fact is now true, and the Commander describes the
extermination of a species in Crateris that no human at Earth has observed and
the player may never have discovered. He has become omniscient.

**Time is evaluated in Python, never delegated to the model.** The character is
told today's date in one sentence so its tenses are right. It is never given a
fact plus a rule about when to use it: an item outside its window is simply
absent from the prompt.

---

## 5. Our model

Three tiers, in increasing volatility (this supersedes
[ai-architecture.md](ai-architecture.md) §6, which described a
`PHRASE_ENABLED` filter that never shipped):

| tier | source | changes |
|---|---|---|
| **Persona** | authored — voice, temperament, worldview | never |
| **Canonical** | the character's own NPC lines, as fact *and* as voice reference | per character |
| **Permitted** | what the story has unlocked, evaluated per turn | every turn |

Tier 3 is computed as:

```
permitted = phrases spoken already this conversation        (the floor)
          | knowledge items whose condition holds
          | lore items whose condition and time window both hold
```

**Default deny.** An item whose condition is not satisfied is not put in the
prompt. There is no instruction to withhold, because the base model already
knows Star Control II and an instruction is not a mechanism. A character with no
authored file behaves exactly as Fwiffo does today, so rollout is per character
and adding an item can only ever widen — under a condition a human wrote and a
test checks.

### The condition grammar

Deliberately tiny. An expression language here is a way for authored data to
acquire authority it must not have.

```
always                     true from before the game begins
<FLAG> <op> <value>        one named flag; ops == != >= <= > <
date >= YYYY-MM-DD         the in-game calendar
```

A list is a conjunction. An unknown flag evaluates to 0, matching
`getGameStateUint`'s own contract; a malformed expression is fatal at load, not
at request time.

### Rules for authored lore

Full Star Control II canon is permitted, and accuracy is required. Two rules
make that safe:

1. **No authored lore may name a star, constellation, coordinate, the captain,
   or the ship.** MegaMod's StarSeed reseeds the map, and the 222 `<% ... %>`
   interpolations exist precisely so the shipped dialogue stays truthful under
   reseeding. A hand-written "the Sa-Matra is in Delta Crateris" is flatly wrong
   in a seeded game and will sound authoritative. Spatial facts come from
   shipped phrases, which the game interpolates. The same rule kills the
   "Zelnick"/"Vindicator" class of error, since both are player-chosen.
2. **Every lore item carries its provenance** — how *this character* came to
   know it. Without it the model invents a chain of custody, and the invented
   chain is itself a spoiler. With it, the model has a true answer to "how do
   you know that?", which is the second question every interrogating player
   asks.

And one addition that earns its place: **denials**. An interrogable character
needs an authored set of things it knows it does *not* know. Without them the
model improvises ignorance differently every turn — sometimes a blank stare,
sometimes a suspiciously well-informed hedge.

---

## 6. Auditing a prompt

When a character says something it should not have, work down this list. Most
apparent model failures are not model failures.

**1. Was the fact even in the prompt?** The sidecar logs the permitted set per
turn. If the token is absent from the prompt, the model invented it, and the
fix is persona or the grounding clause — not the gate.

**2. If it was in the prompt, which item admitted it?** Every knowledge and lore
item has an id, and the log names the satisfied ones. Read that item's condition
against the state that turn.

**3. Was the condition wrong, or the state surprising?** Check the flag's
comment in `globdata.h:237-980` — multi-value flags are the usual culprit, e.g.
`CHMMR_BOMB_STATE >= 1` versus `== 1`. A flag name that does not exist in
`gameStateBitMap` reads as 0 forever and fails *closed*, so a condition that
never unlocks is usually a typo.

**4. Was it a time problem rather than a state problem?** See [§4](#4-time-is-a-separate-axis).
A fact that is true but not yet knowable is the failure this model exists to
prevent, and it looks exactly like a spoiler.

**5. Was it an action, not prose?** If the character *did* something wrong
rather than *said* something wrong, the gate is irrelevant. Actions are
validated twice — in Python and authoritatively in C against the refs exported
that turn — so read the `AI: offering ref N` lines first. The model can only
ever pick from what the encounter exported; it cannot invent one or make an
unavailable one available.

**6. Was the authored text itself the problem?** Under StarSeed the AI capture
branch receives raw `<% ... %>` syntax (`commglue.c:208-218`), and interpolated
text is meaning-only in any case. If the character quoted a star name, suspect
this before suspecting the model.

The discipline that makes this work: **read the log before theorising.** Every
confidently-asserted diagnosis in this project that was not measured turned out
to be wrong.
