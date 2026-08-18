# AI Edition — AI Subsystem Architecture

Design for the AI conversation layer, derived from the source trace in
[fwiffo-conversation-flow.md](fwiffo-conversation-flow.md).

Status: design. Nothing here is implemented yet.

---

## 1. The invariant

> The authored game owns all game state. The AI owns language.

Everything below exists to keep that true under adversarial or simply broken model output.
Concretely: the AI can *select* an action the encounter has already exported, and it can
*generate prose*. It can do nothing else. There is no path from model output to a
`SET_GAME_STATE`, a Lua chunk, a file path, or a shell command.

---

## 2. Process topology

```
+-----------------------------+          +------------------------------+
| UrQuanMasters.exe (32-bit)  |  stdio   | uqm-ai sidecar (64-bit)      |
|                             | <------> |                              |
| game state, rendering,      |  NDJSON  | LLM provider                 |
| action validation           |          | TTS provider                 |
|                             |  WAV via | persona + memory             |
|                             |  temp    | prompt assembly              |
+-----------------------------+  files   +------------------------------+
```

The game spawns the sidecar as a child process and speaks newline-delimited JSON over
stdin/stdout. One JSON object per line, no embedded newlines.

Rationale for out-of-process is recorded in [build.md](build.md): the game is 32-bit and
PyTorch has no 32-bit Windows build, but the deciding reasons are durable ones — crash
isolation, keeping ML dependencies out of a GPL-2.0 binary, and being able to develop and
test the AI service without launching the game.

**Audio is passed by file path, not inline.** Base64 in an NDJSON line would balloon the
transport for a multi-second WAV. The sidecar writes to a temp file and sends the path;
the game plays it through its existing audio facilities and deletes it.

---

## 3. Wire protocol

### 3.1 Handshake

The game must never assume the sidecar is present or capable.

```json
{"type":"hello","protocol":1,"game_version":"0.8.5"}
{"type":"ready","protocol":1,"llm":true,"tts":true,"provider":"mock"}
```

`llm` and `tts` are independent. `tts:false` is a normal, supported state: the
conversation runs with generated subtitles and no generated voice.

### 3.2 Conversation turn

Game to sidecar:

```json
{
  "type": "converse",
  "id": 42,
  "session": {
    "save_id": "slot3:8f2c1a",
    "state_fingerprint": "a91e...",
    "character": "fwiffo",
    "encounter": "SPATHI_PLUTO"
  },
  "player_input": "Fwiffo, calm down. Who are these Ur-Quan?",
  "actions": [
    {"id":"what_about_yourself","text":"Tell me about yourself.","terminal":false},
    {"id":"where_are_urquan","text":"Where are the Ur-Quan now?","terminal":false},
    {"id":"join_us","text":"Come with me.","terminal":true},
    {"id":"changed_mind","text":"Never mind. Goodbye.","terminal":true}
  ],
  "context": {
    "visits": 1,
    "available_knowledge": ["ABOUT_20_YEARS_AGO","URQUAN_LEFT","DREW_SHORT_STRAW"],
    "memory": ["First meeting. Player found Fwiffo hiding; he panicked, then calmed."]
  }
}
```

Every field of `actions` comes straight from `pES->response_list` — id, canonical text,
and whether its callback is `ExitConversation`. Nothing is hand-maintained per race.

Sidecar to game:

```json
{
  "type": "converse",
  "id": 42,
  "spoken_text": "The Ur-Quan?! Oh, they are only the most terrifying...",
  "action": null,
  "remember": null,
  "audio": {"format":"wav","path":"<temp>/uqm-ai-42.wav"}
}
```

`action` is either `null` or exactly one `id` from the actions list in the request.
`remember` is an optional one-line summary fragment (see section 5).

### 3.3 Errors

```json
{"type":"error","id":42,"code":"llm_timeout","message":"provider did not respond in 20s"}
```

The game treats any error, malformed line, or timeout identically: fall back (section 7).
The sidecar is never permitted to stall a conversation indefinitely.

---

## 4. Validation — failing closed

Applied by the **game**, in C, before anything is acted on. The sidecar is untrusted.

| Field | Rule on violation |
|---|---|
| `action` | Must match an id sent this turn. Unknown, stale, or malformed becomes `null`, prose is kept |
| `spoken_text` | Non-empty, length-capped, control characters stripped |
| `remember` | Length-capped; dropped if over budget (section 5) |
| `audio.path` | Must be the temp path the game expects; never a model-supplied path |
| `id` | Must correlate to the outstanding request; late replies discarded |

A rejected action degrades to conversation without a state transition — the strictly safer
failure. This is what makes an invented `destroy_the_universe` a non-event rather than a
bug: the encounter never exported it, so there is no handler to call.

**Actions are validated against the list sent this turn, not a global registry.** Because
`SelectResponse` clears `response_list` before dispatch, a stale action from a previous
turn is rejected by construction.

---

## 5. Memory

Per-character, per-save, and **summarised — never verbatim transcript**.

At the end of each encounter the sidecar folds the conversation into one short summary
entry. Prompt growth is therefore bounded by entry count, not by how long the player talks.

```
memory[save_id][character] = [
  "First meeting. Player found Fwiffo hiding; he panicked, then calmed.",
  "Player asked about the Ur-Quan. Fwiffo bragged, then admitted he hid."
]
```

Two hard rules:

1. **Memory is keyed to the save, not the character alone.** Game memory such as
   `SPATHI_VISITS` lives inside the save file. A side-file keyed only by character would
   desynchronise: load an earlier save and the character recalls a conversation that has
   not happened in that timeline. `state_fingerprint` lets the sidecar detect a memory
   newer than the loaded state and truncate it.
2. **Memory may never influence game state.** It colours dialogue only. Because the sole
   state-changing path is `action`, and `action` is validated against the exported set,
   this holds regardless of what the model writes into memory.

Bounded by entry count and total characters; oldest entries are folded or dropped.

---

## 6. Prompt assembly

Three tiers, in increasing volatility:

| Tier | Source | Changes |
|---|---|---|
| **Persona** | Authored per character — voice, temperament, verbal tics, worldview | Never |
| **Canonical knowledge** | The character's NPC phrase texts, as both fact and voice reference | Per character |
| **Permitted knowledge** | Filtered by `PHRASE_ENABLED` at request time | Every turn |

The third tier is the anti-spoiler mechanism, and it matters because the base model
already knows Star Control II. Fwiffo cannot discuss the Spathiwa coordinates before that
branch unlocks, because the game does not send them. The defence is derived from game
state rather than trusted to an instruction such as "do not reveal spoilers".

When asked something outside his knowledge, the character must respond in character —
admit ignorance, deflect, change the subject, or lie if that suits him — never invent
canon.

---

## 7. Failure handling

AI is optional infrastructure. Gameplay must never become inconsistent because of it.

```
AI text + generated voice
        |  (TTS unavailable / times out)
        v
AI text + no voice
        |  (LLM unavailable / malformed / timeout)
        v
original dialogue menu
```

The third tier is why the original conversation system stays runnable rather than being
replaced. Failure cases to handle explicitly: sidecar not installed, model missing or
still downloading, LLM timeout, malformed JSON, unknown action, TTS failure, provider auth
failure, quota exhausted, unsupported hardware.

---

## 8. Provider abstraction

Two independent interfaces. A provider's wire format never reaches game code.

```
      LLMProvider                       TTSProvider
           |                                 |
   +-------+--------+              +---------+---------+
   |                |              |                   |
 Local          Subscription     Cloning            Fixed-voice
 (llama.cpp)    (adapters)       (Fwiffo ref)       (native fallback)
```

The internal contract is **schema-constrained JSON**, not any provider's native
tool-calling format. Native tool-calling may be used as an encoding where a provider does
it well, but it is never required — tool-calling is exactly where small local models are
weakest, and local models are first-class.

Conceptually the model has two tools:

| Tool | Bound |
|---|---|
| `perform_action(id)` | Must be in this turn's action list |
| `remember(fact)` | Length- and count-capped; cannot touch game state |

Since the game is and remains open source, **local inference is the default**: no account,
no terms question, no per-player cost, and contributors are never required to hold a
subscription. Subscription-backed providers are an additional tier, not a dependency.

---

## 9. Voice

Generated lines use Fwiffo's **original voice**, cloned from the 3DO recordings the game
already ships.

The Spathi voice set covers both Fwiffo on Pluto and the Spathi homeworld characters. They
can be separated precisely because `mm-3dovoice/spathi/spathi.ts` is keyed by the same
enum as `strings.h`: Fwiffo's Pluto lines (`I_FWIFFO`, `WEZZY_WEZZAH`,
`MUST_DO_RITUAL_AT_HOME`, `DREW_SHORT_STRAW`) form a clean reference set distinct from the
Spathiwa lines.

A hybrid strategy, better than pure synthesis on every axis that matters:

```
AI response
   |
   +-- action maps to an existing canonical line --> play the original recording
   |                                                 (real actor, zero latency)
   |
   +-- novel free-form prose --------------------> cloned TTS + species DSP
```

Species DSP (pitch/formant shift, filtering, modulation) is a separate stage after TTS, so
alien identity does not depend solely on the voice model. Not required for the MVP.

Shipping synthesised speech that imitates the original voice actors is an unresolved
*release* question, distinct from development use of content already redistributed with
the game. The hybrid narrows the exposure, since canonical lines are the genuine
recordings and only novel prose is synthetic.

---

## 10. Build order

1. **Mock provider.** Deterministic rules — input containing "join" selects `join_us` —
   with fixed generated-looking prose. Proves the entire game integration with no model
   involved, and gives the regression tests something stable to assert against.
2. **Sidecar and protocol**, still mock-backed.
3. **Real local LLM** behind `LLMProvider`.
4. **TTS** behind `TTSProvider`.
5. **Subscription adapters**, if terms permit.

Tests worth having before step 3: an invalid action performs no mutation; malformed output
leaves the game stable; a canonical action produces the same state as the equivalent menu
choice; AI unavailable still allows normal play; TTS unavailable still allows conversation.
Recorded request/response fixtures let integration tests run without a live model.
