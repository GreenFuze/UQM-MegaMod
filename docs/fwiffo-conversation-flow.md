# The Fwiffo Encounter — End-to-End Trace

How a conversation actually works in MegaMod 0.8.5, traced through source rather than
inferred. This is the basis for the AI Edition interception design.

Subject: the Spathi **Fwiffo**, encountered alone on Pluto.

---

## 1. Where the code lives

| Path | Contents |
|---|---|
| [`src/uqm/comm/spathi/spathic.c`](../src/uqm/comm/spathi/spathic.c) | The Spathi encounter: state machine, responses, NPC lines |
| [`src/uqm/comm/spathi/strings.h`](../src/uqm/comm/spathi/strings.h) | `enum` of every phrase ID (both NPC lines and player options) |
| [`src/uqm/commglue.h`](../src/uqm/commglue.h) | The four conversation macros |
| [`src/uqm/comm.c`](../src/uqm/comm.c) | Generic conversation engine — response storage, input, dispatch |
| [`src/uqm/globdata.h`](../src/uqm/globdata.h) | Persistent game state bit-fields (`ADD_GAME_STATE`) |

**All AI Edition interception happens in `comm.c`.** The per-race files are untouched,
which means the mechanism is race-agnostic from day one — Fwiffo is the first subject,
not a special case.

---

## 2. The four primitives

From [`commglue.h`](../src/uqm/commglue.h):

```c
#define PLAYER_SAID(r,i)  ((r)==(i))
#define Response(i,a)     DoResponsePhrase(i, (RESPONSE_FUNC)a, 0)
#define NPCPhrase(index)  NPCPhrase_cb((index), NULL)

#define PHRASE_ENABLED(p) (*GetStringAddress(...ConversationPhrases, (p)-1) != '\0')
#define DISABLE_PHRASE(p) (*GetStringAddress(...ConversationPhrases, (p)-1)  = '\0')
```

| Primitive | Role |
|---|---|
| `PLAYER_SAID(R, x)` | Dispatch on which response the player chose |
| `Response(id, cb)` | Register one available player action and its handler |
| `NPCPhrase(id)` | Emit an NPC line (subtitle + voice) |
| `PHRASE_ENABLED(id)` | Is this option currently offered? |
| `DISABLE_PHRASE(id)` | Retire an option permanently |

> **`DISABLE_PHRASE` blanks the string itself.** "Disabled" literally means the phrase's
> text is set to empty, and `PHRASE_ENABLED` is a non-empty test on that same string.
> Anything reading phrase text must tolerate empty strings rather than assuming a valid
> option.

Phrase IDs come from one `enum` per race. The convention is load-bearing:

```c
SORRY_ABOUT_THAT,   /* UPPERCASE = NPC line   (voiced) */
identify,           /* lowercase = player option (unvoiced) */
I_FWIFFO,
hi_there,
```

The same enum indexes the 3DO voice pack (`content/addons/mm-3dovoice/spathi/spathi.ts`),
so a phrase ID maps to canonical text *and* to the original recording.

---

## 3. An encounter handler

[`spathic.c:410`](../src/uqm/comm/spathi/spathic.c) — representative of every handler:

```c
static void SpathiMustGrovel (RESPONSE_REF R)
{
    if (PLAYER_SAID (R, identify))
    {
        NPCPhrase (I_FWIFFO);                        // what Fwiffo says
        Response (do_cultural,    SpathiMustGrovel); // ┐
        Response (youre_forgiven, SpathiOnPluto);    // ├ what the player may do next
        Response (die_slugboy,    ExitConversation); // ┘
    }
    else if (PLAYER_SAID (R, do_cultural)) { ... }
}
```

A handler does exactly two things: emit NPC lines, and register the actions now available.
The registered set is the encounter's complete, current action surface.

---

## 4. The conversation loop

```
   handler(R)
     ├── NPCPhrase(id) ......... queue NPC subtitle + voice
     └── Response(id, cb) × N .. DoResponsePhrase → pES->response_list
              │
              ▼
   PlayerResponseInput(pES) .... navigate the menu, set pES->cur_response
              │
              ▼
   SelectResponse(pES) ......... echo player text, clear list, dispatch
     └── response_func(response_ref)
              │
              └──────────────► back to handler(R)
```

### 4a. Registration — `DoResponsePhrase` ([comm.c:1642](../src/uqm/comm.c))

```c
pEntry = &pES->response_list[pES->num_responses];
pEntry->response_ref       = R;              // stable action ID
pEntry->response_text.pStr = <string table>; // canonical option text
pEntry->response_func      = response_func;  // handler; ExitConversation ⇒ terminal
++pES->num_responses;
```

`pES->response_list[0 .. num_responses-1]` carries everything the AI layer needs:
a stable identifier, human-readable semantics, and whether taking it ends the conversation.

Lua appears in this function only for **string interpolation**
(`luaUqm_comm_stringInterpolate`) — it does not control conversation flow.

### 4b. Selection — `PlayerResponseInput` ([comm.c:1476](../src/uqm/comm.c))

Cursor navigation over `response_list`, bounded by `num_responses`, writing
`pES->cur_response`. **This is the function AI mode replaces with a text field.**

### 4c. Dispatch — `SelectResponse` ([comm.c:1412](../src/uqm/comm.c))

```c
utf8StringCopy (pES->phrase_buf, ..., response_text->pStr);
FeedbackPlayerPhrase (pES->phrase_buf);   // show the player's line
pES->num_responses = 0;
ClearResponses (pES);
(*pES->response_list[pES->cur_response].response_func)
        (pES->response_list[pES->cur_response].response_ref);
```

The list is cleared *before* dispatch, so the handler repopulates it from scratch. The
action set is therefore rebuilt every turn and never stale.

---

## 5. Termination and outcomes

There is no separate "conversation is over" flag. **Termination is a property of the
action**, carried by its callback. `ExitConversation`
([spathic.c:153](../src/uqm/comm/spathi/spathic.c)) decides what the ending *means*:

| Player action | Outcome |
|---|---|
| `die_slugboy`, `we_fight_1`, `we_fight_2`, `pay_for_crimes`, `tell_me_coordinates`, `changed_mind` | `setSegue (Segue_hostile)` → **combat** |
| `join_us` | `AddEscortShips (SPATHI_SHIP, 1)` + `SetEscortCrewComplement(...)` → **Fwiffo joins the fleet** |

`join_us` is the canonical `recruit_fwiffo`, and it shows the deterministic layer
overruling intent:

```c
if (EscortFeasibilityStudy (SPATHI_SHIP) == 0)
    NPCPhrase (TOO_SCARY);      // refused — no room in the fleet
else
    NPCPhrase (WILL_JOIN);      // recruited
```

Even a correctly-chosen action can be refused by game state the AI never sees.

---

## 6. Persistent state

State lives in bit-fields declared in [`globdata.h`](../src/uqm/globdata.h) and is
serialized into the save file:

```c
ADD_GAME_STATE (SPATHI_VISITS, 3)       // 3 bits
ADD_GAME_STATE (FOUND_PLUTO_SPATHI, 2)
```

Read and written with `GET_GAME_STATE` / `SET_GAME_STATE`. `SPATHI_VISITS` already drives
first-meeting vs returning-visitor greetings (`INIT_*_HELLO` vs `SUBSEQUENT_*_HELLO`) —
the game's own, very coarse, per-character memory.

**Implication for AI memory:** because game memory lives *inside the save*, any AI-side
conversation memory must be keyed to the save as well. Otherwise loading an earlier save
leaves the character remembering a conversation that has not happened in that timeline.

---

## 7. Fwiffo's knowledge boundary is enumerable

His NPC phrases define what he can speak to:

```
ABOUT_20_YEARS_AGO   WHEN_URQUAN_ARRIVED   URQUAN_LEFT     ABOUT_OTHER_RACES
ABOUT_MYSELF         STATIONED_ON_EARTH_MOON   BLAZE_IS    SET_UP_BASE
ABOUT_ILWRATH        SPATHI_ARE            ENEMY_IS        THEN_ILWRATH
DREW_SHORT_STRAW     JUST_ME               THOUSANDS       HOW_TRUE
```

The union of these is his knowledge; their canonical text is both the factual record and
the voice. `PHRASE_ENABLED` narrows that to what is available *right now*, which gives
anti-spoiler enforcement derived from game state rather than trusted to the prompt.

---

## 8. Interception points for AI Edition

Three hooks, all in `comm.c`, all race-agnostic:

| # | Location | Change |
|---|---|---|
| 1 | `PlayerResponseInput` | Replace menu navigation with a free-text input field |
| 2 | `SelectResponse` | Take `cur_response` from the validated AI choice instead of cursor position |
| 3 | `NPCPhrase_cb` | Render generated text as subtitle, and generated audio in place of the recorded clip |

The request sent to the AI sidecar is assembled from `response_list` — which already
contains the action IDs, their canonical text, and their terminal flags. Nothing needs to
be hand-maintained per race, and an action the model invents simply has no entry, so it
fails closed.
