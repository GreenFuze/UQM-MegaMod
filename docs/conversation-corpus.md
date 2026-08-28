# The conversation corpus

What exists, what it is called, how to resolve it, and where it will bite you.

Every figure here was measured from the trees at MegaMod 0.8.5, not estimated.
See [Reproducing these numbers](#reproducing-these-numbers).

---

## 1. Shape

27 characters. 3,377 phrases: **2,275 NPC lines** and 1,102 player options.
**88,916 words of NPC dialogue** — about a short novel. 2,557 voice clips.
222 phrases carry a MegaMod `<% ... %>` interpolation.

`robot/` is not in that count and is not a character: it is a phoneme table for
the starbase computer (`ROBOT_DIGIT_0`, unit words), its enum lives in
`commglue.h:147` rather than a `strings.h`, and it has no conversation `.c`.
Any scan keyed on the conversation source excludes it for free.

| source | content | phrases | NPC | NPC words | clips | interp | flags read | written |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| starbas | starbase | 267 | 207 | 8886 | 226 | 56 | **73** | 33 |
| melnorm | melnorme | 281 | 210 | 7609 | 263 | 26 | 35 | 34 |
| pkunk | pkunk | 180 | 124 | 6286 | 149 | 10 | 22 | 19 |
| thradd | thraddash | 152 | 109 | 5448 | 124 | 9 | 21 | 17 |
| utwig | utwig | 114 | 76 | 5366 | 92 | 11 | 17 | 19 |
| ilwrath | ilwrath | 109 | 67 | 4015 | 79 | 6 | 10 | 7 |
| spathi | spathi | 135 | 72 | 3993 | 86 | 6 | 8 | 8 |
| syreen | syreen | 127 | 73 | 3894 | 87 | 13 | 18 | 14 |
| talkpet | talkingpet | 112 | 76 | 3854 | 77 | 3 | 14 | 17 |
| druuge | druuge | 105 | 78 | 3328 | 80 | 2 | 30 | 28 |
| zoqfot | zoqfotpik | 334 | 305 | 3252 | 312 | 11 | 9 | 6 |
| orz | orz | 114 | 75 | 3192 | 80 | 4 | 12 | 12 |
| arilou | arilou | 97 | 61 | 3190 | 76 | 8 | 21 | 23 |
| slyhome | slylandro | 114 | 60 | 3104 | 70 | 3 | 22 | 22 |
| vux | vux | 102 | 61 | 2972 | 77 | 6 | 14 | 14 |
| spahome | safeones | 143 | 80 | 2829 | 87 | 4 | 12 | 13 |
| urquan | urquan | 76 | 44 | 2042 | 43 | 0 | 12 | 9 |
| yehat | yehat | 68 | 40 | 1991 | 43 | 5 | 19 | 14 |
| supox | supox | 93 | 57 | 1936 | 75 | 13 | 14 | 9 |
| chmmr | chmmr | 78 | 46 | 1868 | 57 | 5 | 10 | 7 |
| comandr | commander | 94 | 48 | 1852 | 52 | 6 | 9 | 9 |
| umgah | umgah | 86 | 56 | 1790 | 63 | 5 | 11 | 8 |
| blackur | kohrah | 76 | 46 | 1778 | 44 | 0 | 11 | 8 |
| rebel | yehatrebels | 34 | 23 | 1574 | 27 | 2 | 6 | 3 |
| mycon | mycon | 109 | 69 | 1322 | 73 | 4 | 13 | 9 |
| shofixt | shofixti | 91 | 47 | 1202 | 50 | 4 | 7 | 7 |
| slyland | probe | 86 | 65 | 343 | 65 | 0 | 8 | 7 |

Two files carry 18.5% of all NPC text. `zoqfotpik` has the most phrases and
nearly the least prose, because Zoq and Pik alternate speakers and every
utterance is split into `_0/_1/_2` fragments dispatched through per-speaker
callbacks (`zoqfotc.c:817-824`). `probe` is 343 words of machine barks with a
single conversation node and no player agency.

---

## 2. Who these characters actually are

The directory names mislead. Confirmed from `resinst.h` and the LOCDATA structs:

| source | who it is |
|---|---|
| `comandr` | Commander Hayes, **before** the starbase exists |
| `starbas` | Commander Hayes, **after** it exists — same person, later in the story |
| `spathi` | Fwiffo, alone at Pluto |
| `spahome` | the Spathi homeworld, the "Safe Ones" |
| `urquan` | Ur-Quan **Kzer-Za** (green) |
| `blackur` | Ur-Quan **Kohr-Ah** (black) |
| `yehat` | Yehat Royalists |
| `rebel` | Yehat Rebels |
| `slyland` | the Slylandro **probe** |
| `slyhome` | the Slylandro **homeworld** gasbags |
| `talkpet` | the Dnyarri "Talking Pet" |

`comandr`/`starbas` is the important one. **The shipped game already models
"same character, different point in the story, different knowledge" — as two
separate files**, selected by `commglue.c:625-629` on `STARBASE_AVAILABLE`.
`SPATHI_CONVERSATION` forks the same way on `GLOBAL_FLAGS_AND_DATA` bit 7.

---

## 3. Resolving a character to its files

Never hand-write this map. Twelve content directories are named differently
from their source directory, and **`probe`/`slylandro` are swapped**: source
`slyland` is the probe, source `slyhome` is the homeworld. A transcription
error attributes an entire character's words to the wrong species.

The chain, implemented in [`ai/uqm_ai/cast.py`](../ai/uqm_ai/cast.py):

```
src/uqm/comm/<dir>/*.c     the LOCDATA line marked  /* PlayerPhrases */  names a macro
src/uqm/comm/*/resinst.h   that macro expands to a resource name
uqm.rmp                    the resource name maps to base/comm/<name>/<name>.txt
```

Scan every `resinst.h` as **one namespace**: `spahome`'s dialogue macro is
declared in `spathi/resinst.h`, not its own.

The resource name is also the character's identity on the wire, because
`LOCDATA.ConversationPhrasesRes` (`globdata.h:127-177`) holds exactly that
string at runtime and `CommData` is public. It is already correct across both
forks above. Nothing per-race had to be added to the game to obtain it.

---

## 4. File format

```
#(PHRASE_KEY)<TAB>voice-clip.ogg
first line of the phrase
second line of the phrase
                              <- blank line ends the phrase
```

Parser: `_GetConversationData()` in `src/libs/strings/getstr.c:116`.

- A line whose **first character is `#`** is a header. The key is between the
  first `(` and the next `)`; anything after `)` is the clip filename.
- The separator before the clip is **TAB or a single space** — both occur.
  Match on one-or-more of either.
- **Case encodes the speaker.** UPPERCASE keys are NPC lines, lowercase are
  player options. This convention is load-bearing and universal.
- **There is no comment syntax.** Every `#` line is a header.
- **Line breaks are authored, not wrapped** — the newline is the subtitle
  break. Joining lines with a space produces run-on sentences with missing
  punctuation, because source lines end mid-sentence without a comma.
- 1,102 headers have no clip; those are the player options.

Keys match the `strings.h` enum name-for-name and position-for-position, but
**only position is authoritative** — the game resolves a phrase as
`SetAbsStringTableIndex (CommData.ConversationPhrases, R - 1)`.

The addon `.txt` files under `addons/mm-3dovoice/<race>/` exist for six races
and split long speeches into sub-clips. **Always load the `base/` file.** The
override key lists are identical for arilou/melnorme/mycon/syreen/utwig, and
starbase's override is a trailing truncation.

---

## 5. Traps, all of them confirmed in the tree

**`#if 0` inside the enum.** `pkunk/strings.h:103-107` wraps `NOT_CONQUER_10`,
`_11` and `_12` in a disabled block. The compiler never emits them. A parser
that counts them puts every later phrase three indices low and misattributes
every line after that point. This is the only such block in 27 headers, and it
produced a silently-wrong table until `StringsHeader` learned to skip it.

**An enum member with no dialogue line.** `umgah/strings.h:110` declares
`OUT_TAKES`, `umgahc.c:511` speaks it, and `umgah.txt` has no entry — the stock
game reads off the end of its own table. Tolerate a short tail; a tail cannot
shift an earlier index. A dialogue file **longer** than the enum is an
insertion and must stay fatal.

**Renamed keys at matching positions.** MegaMod's artifact randomisation
renames starbase indices 151 and 152 (`ABOUT_WIMBLIS_TRIDENT` /
`ABOUT_GLOWING_ROD` in the enum, `ABOUT_ARTIFACT_2` / `_3` in the content).
Handle with an explicit per-index alias, never a blanket relaxation: a genuine
shift mismatches in a long run nobody would alias by hand.

**Deliberately wordless NPC phrases.** Five exist and are spoken on purpose as
silent sequence terminators — mycon `AMBUSH_TAIL` and `RAMBLE_TAIL`, talkpet
`HYPNO_TAIL`, thradd `NAME_TAIL`, and starbase `BLANK`, which even ships a
`null.ogg`. They must resolve as refs and must never reach a prompt, where they
would appear as an empty pair of quotation marks attributed to the character.

**`<% ... %>` interpolation.** 222 phrases carry one. Eight functions are used;
`getStarName`, `getConstellation`, `getColor`, `getPoint` and `swapIfSeeded`
all take the canonical 1992 value as their first string argument.
`getCaptainName` and `getShipName` are player-chosen. Under StarSeed the
canonical value is **wrong**, so interpolated text is meaning-only and its
specifics must never be quoted as fact. See
[character-knowledge-model.md](character-knowledge-model.md) for the rule this
implies for authored lore.

**`MAX_RESPONSES` is 8** (`comm.c:55`) and `DoResponsePhrase` (`comm.c:2128`)
writes `response_list[num_responses]` with **no bounds check**. The cap is
enforced by authoring discipline alone. Count before adding any `Response()`
call to a race in AI mode.

---

## Reproducing these numbers

```bash
python -m pytest ai/tests/test_cast.py -q
```

`ai/tests/test_cast.py` asserts the per-character phrase counts in the table
above, that refs are dense and one-based, and that no wordless phrase can reach
a prompt. It is the regression guard for the whole join; if the content package
or the enums drift, it fails there rather than in a conversation.
