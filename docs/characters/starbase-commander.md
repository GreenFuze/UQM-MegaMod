# Commander Hayes: what he knows, and when

The knowledge map for one character, in the order the game reveals it. Every
condition here was read out of `src/uqm/comm/comandr/comandr.c` and
`src/uqm/comm/starbas/starbas.c`; every quotation is his own shipped dialogue.

He is the right character to map first. He reads **73 distinct game-state
flags** — more than any other, nine times Fwiffo's eight — because he is
structurally a briefing rather than a conversation, and he is the one the player
comes back to a dozen times.

---

## 1. One man, two files

The game splits him in two and swaps on a single flag, `commglue.c:625-629`:

```c
case COMMANDER_CONVERSATION:
    if (!GET_GAME_STATE (STARBASE_AVAILABLE))
        return init_commander_comm ();   // comandr/ — 94 phrases
    else
        return init_starbase_comm ();    // starbas/ — 267 phrases
```

This is the shipped game already modelling *the same person knowing different
things at different points in the story*. It is not a design we are imposing;
it is the one we are extending.

| | before | after |
|---|---|---|
| source | `comandr` | `starbas` |
| phrases | 94 | 267 |
| NPC words | 1,852 | 8,886 |
| flags read | 9 | 73 |

---

## 2. Phase one — the besieged station

**Who he is.** "I am Starbase Commander Hayes of the slave planet Earth."

**What is true.** Energy cores exhausted. Scanners and deep radar non-functional
— he cannot identify an approaching vessel and asks whether it is the scheduled
Hierarchy resupply ship. Under the Oath of Fealty his crew must maintain the
starbase for the Ur-Quan. They have no ships of their own. Resupply was promised
at five-year intervals and nothing has come in almost eight years.

**What he does not know, and this is the point of the phase:**

- what is happening on Earth — the slave shield blocks everything, including
  communication;
- what is on the Hierarchy moon base, which he knows exists and cannot contact;
- anything about the wider war, other races, or what the captain should do.

He is a man in a dead room. Almost every interesting question in the game gets
"I don't know" from him here, and that is correct characterisation rather than a
gap to be filled.

**The beats.**

| beat | gate | what changes |
|---|---|---|
| first contact | — | he mistakes the flagship for a resupply ship |
| the explanation | — | `THE_WHAT_FROM_WHERE` — he gives the whole situation in one speech |
| radioactives delivered | `RADIOACTIVES_PROVIDED` | the station lives; `ABOUT_TIME` |
| naming the alliance | after the above | The New Alliance of Free Stars, or the Concordance |
| fuel and landers | `GIVEN_FUEL_BEFORE`, `LANDERS_LOST` | small, repeatable, and he gets sharper about lost landers |

`STARBASE_AVAILABLE` flips and he becomes a different file.

---

## 3. Phase two — the war effort

267 phrases, and the knowledge divides into four systems that behave quite
differently. Only the first is what most players ever see.

### 3.1 The mission-advice ladder — `AnalyzeCondition`, starbas.c:790-910

The answer to *"what should I do next?"* It is a decision tree over ship state
and plot flags, and it is the cleanest demonstration of knowledge changing with
the story.

| rung | condition | he says |
|---|---|---|
| 1 | flagship below minimum, `CHMMR_BOMB_STATE < 2` | build up and balance the flagship; gather minerals, or spend the RU you have |
| 2 | fewer than 2 alien allies | "investigate building alliances with non-hostile alien races. Their assistance is crucial" |
| 3 | 2+ allies, fleet under strength | buy combat ships, or get them by mining and alliance |
| 4 | fleet adequate, `!AWARE_OF_SAMATRA` | "We must find a chink in the Ur-Quan's armor" |
| 5 | `AWARE_OF_SAMATRA`, `!UTWIG_BOMB` | "You know the Ur-Quan's Achilles' heel… You must find some way to destroy the Sa-Matra" |
| 6 | `CHMMR_BOMB_STATE >= 2` | the Chmmr have improved the bomb; assemble a powerful fleet |
| 7 | above, plus `TALKING_PET_ON_SHIP` | "Go attack the Ur-Quan Sa-Matra vessel!" |

**Note what rungs 1-3 depend on.** `HasMinimum`, `FleetStrength` and the ally
count are computed from ship and fleet state, not from any saved flag. They are
not in `gameStateBitMap`, which is why the wire carries six derived `SIS_`
values alongside the flags.

### 3.2 The bulletins — the key points in the game's process

`CheckBulletins`, starbas.c:1463-1690. A 32-bit register, `STARBASE_BULLETS`,
one bit per news item, set once the item has fired. This is the most explicit
"what have I already told you" bookkeeping in the game, and it exists for this
character alone.

**23 of the 32 slots are used.** Bits 16, 19, 20, 22-25, 30 and 31 are empty.

Three behaviours worth understanding before reading the table:

- **He does not tell you what you already found out.** Several bulletins set
  their own bit without firing — `if (GET_GAME_STATE (MET_MELNORME))
  BulletinMask |= 1L << b0;` — so the news is retired unheard if the captain got
  there first. That is a knowledge-model behaviour, not bookkeeping.
- **Some are on a clock.** `CheckTiming(months, days)` measures elapsed time
  since `STARBASE_MONTH`/`STARBASE_DAY`, i.e. since the base became yours.
- **Asking to hear them again inverts the mask**, so old bulletins can be
  replayed without re-firing their side effects.

**Alliances — fire when `CheckAlliance(...) == GOOD_GUY`:**

| bit | who | note |
|--:|---|---|
| 0 | Spathi | a delegation arrives under High Council orders |
| 1 | Zoq-Fot-Pik | "your diplomatic efforts have struck gold" |
| 2 | Supox | ship designs, and as many as you want |
| 3 | Utwig | fabricators set up for Juggers |
| 4 | Orz | "invaded… though so far the invasion is a friendly one" |
| 12 | Chmmr | formal alliance; ships buildable |
| 13 | Shofixti | "The Shofixti have returned!" |

**Plot beats — fire on state:**

| bit | gate | the news |
|--:|---|---|
| 5 | `PORTAL_SPAWNER` and a free escort slot | an Arilou fleet arrives and **gifts three ships**. Permanently suppressed if `ARILOU_MANNER == 2` |
| 6 | `ZOQFOT_DISTRESS == 1` | priority distress call from Alpha Tucanae, weak and partial |
| 10 | `SPATHI_SHIELDED_SELVES` | every Spathi captain has vanished from the starbase |
| 14 | `PKUNK_MISSION` | an InterSpace disturbance at 100:50 |
| 17 | `YEHAT_ABSORBED_PKUNK` | six of his people fell unconscious at once, and he nearly did not mention it |
| 18 | `CHMMR_BOMB_STATE == 2` | the bomb is attached and the work is under way |
| 21 | `ZOQFOT_DISTRESS == 2` | "somber news" — no word from the Zoq-Fot-Pik |

**On a clock — and each is retired unheard if the captain got there first:**

| bit | fires after | retired by | the news |
|--:|---|---|---|
| 7 | 1 month (3 on easy) | `MET_MELNORME` | an unknown race called the Melnorme wants to trade |
| 8 | 3 months (6 on easy) | `MET_MELNORME` | the Melnorme have returned, asking when you will meet |
| 9 | 7 days | `FOUND_PLUTO_SPATHI` | faint alien signals from the direction of Uranus |
| 11 | 21 days (42 on easy) | `ZOQFOT_HOME_VISITS` or `ZOQFOT_GRPOFFS` | broad-beam HyperWave from Rigel |
| 15 | 7 months | `DESTRUCT_CODE_ON_SHIP` | the red probe that killed Captain Burton — and there are more of them now |

**Consequences — the only place he judges the captain:**

| bit | threshold | the news |
|--:|---|---|
| 26 | 1..`MIN_SOLD` crew sold to the Druuge | a wild rumour is going around, and he wants it settled |
| 27 | above `MIN_SOLD` | "incontrovertible evidence… Captain, how could you!?" |
| 29 | above `MAX_SOLD` | "you're a heinous slave-trader" |
| 28 | 1,000+ crew purchased | your achievements have created a manpower problem |

`MIN_SOLD` is 100 (200 easy, 10 hard); `MAX_SOLD` is 250 (500, 25) —
`globdata.h:1234`.

### 3.3 The device lab — gated on possession

The starbase analyses what the captain physically brings back, so each report
exists only once the thing is aboard. This is the clearest per-object case of
the same character knowing different things at different times.

`ABOUT_PORTAL`, `ABOUT_TALKPET`, `ABOUT_BOMB`, `ABOUT_SUN`, `ABOUT_MAIDENS`,
`ABOUT_SPHERE`, `ABOUT_HELIX`, `ABOUT_SPINDLE`, `ABOUT_UCASTER`,
`ABOUT_BCASTER`, `ABOUT_SHIELD`, `ABOUT_EGGCASE_0`, `ABOUT_SHUTTLE` — each
paired with its `*_ON_SHIP` flag.

**The Ultron is the one that moves.** `ULTRON_CONDITION` runs 0 (the Supox still
have it) through 5 (returned to the Utwig), and the lab has three different
reports as it is repaired:

| condition | report |
|---|---|
| 1 — completely broken | "At first we thought this was a piece of junk — in fact it may still be" |
| 2 — partially functional | "we can detect energy emissions" |
| 3+ — repaired | it is in fact the "Appendages of the Ultimate Deity" |

Each supersedes the last. A character who still offers the "piece of junk"
reading after the thing works is wrong in a way a player will notice.

### 3.4 The interrogation surface — reachable, and almost never reached

Roughly 88 phrases have **no state gate at all**. They are available at the
starbase from the first visit, behind menus four levels deep that most players
never open:

- **the war and the surrender** — `AFTER_WAR`, `ABOUT_URQUAN`,
  `ABOUT_ALIENS_ON_EARTH`, `ABOUT_PRECURSORS`, `ABOUT_OLD_RACES`;
- **the Alliance races he fought beside** — Chenjesu, Mmrnmhrm, Yehat, Shofixti,
  Syreen, Arilou;
- **the Hierarchy races he fought** — Mycon, Spathi, Umgah, Androsynth, VUX,
  Ilwrath;
- **running the ship** — fuel, modules, crew, shipyards, RU, minerals.

He was a combat pilot on the Coreward Front and every one of these is
first-hand. This is the largest single gain of free text for this character, and
it needs no gating work at all — it only needed to become askable.

> Worth stating because it caught us out: the Syreen and the Chenjesu are **not**
> spoilers for him. `starbas.c:407` lets him describe them from the first visit
> with no state test. Gating them would be stricter than the shipped game.

---

## 4. What he never knows

Authored refusals matter as much as authored knowledge. Without them a character
improvises ignorance differently every turn — a blank stare once, a
suspiciously well-informed hedge the next.

| topic | lifts when |
|---|---|
| what is happening on Earth, or who is alive down there | never |
| where the Ur-Quan superweapon is, or what it is | `AWARE_OF_SAMATRA` |
| Ur-Quan history, origins, or why they take slaves | never — he is a soldier, and this is the Melnorme's trade good |
| what became of the Androsynth | never — he knows they rebelled and left, and nothing after |

The second is the interesting one. Before `AWARE_OF_SAMATRA` he is certain the
Ur-Quan have a weak point and equally certain he has no idea what it is; the
denial and the mission advice say the same thing from two directions.

---

## 5. Notes for the AI treatment

Four things about him that a persona has to carry, all of them visible in the
shipped text:

1. **He leads with the thing that matters.** No preamble, no decoration. The
   long `THE_WHAT_FROM_WHERE` speech is the whole situation in one breath.
2. **He is not deferential.** The captain outranks him in importance, not in
   rank, and he is short with them when they lose a lander.
3. **He does not speculate.** Where he does not know, he says so in one sentence
   — which is why the denials above are in his voice rather than against it.
4. **He is steady rather than hopeful.** He has never let himself believe this
   will work and he keeps working. When things go well he says so once and moves
   to the next problem.

Two gaps in the current authoring, recorded rather than hidden:

- The **bulletins are not yet modelled**. They are the best "already told you"
  signal in the game and the file does not use `STARBASE_BULLETS` at all. That
  is the largest remaining piece of this character.
- **`DISCUSSED_*`** — 21 flags pairing with `*_ON_SHIP` to record whether he has
  already briefed the captain on an object — are likewise unused. They would
  stop him re-delivering an analysis he has already given.
