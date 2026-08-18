# uqm-ai

The AI Edition sidecar and its supporting tools. Runs as a separate 64-bit process;
see [../docs/ai-architecture.md](../docs/ai-architecture.md) for the design.

## uqm_ai.dialogue

Parses a conversation resource (`content/base/comm/<race>/<race>.txt`) into phrases,
recovering each phrase key, its voice clip, and its text.

## uqm_ai.phrase_table

Joins that dialogue file with the race's C phrase enum from
`src/uqm/comm/<race>/strings.h`, reproducing the game's own indexing:

```c
SetAbsStringTableIndex (CommData.ConversationPhrases, (R - 1))
```

Construction **verifies** the two sources agree key-by-key and fails loudly if they do
not. A silent misalignment would attribute the wrong words to the wrong action, which is
exactly the class of bug that is invisible until it matters.

Verified against Spathi/Fwiffo: 135 phrases, 72 NPC (all voiced), 63 player options.

## Running the sidecar

```bash
cd ai
printf '%s\n' '{"type":"hello"}' | python -m uqm_ai
```

It speaks newline-delimited JSON on stdin/stdout — one object per line. See
[../docs/ai-architecture.md](../docs/ai-architecture.md) section 3 for the message shapes.

A worked exchange:

```json
{"type":"converse","id":7,
 "session":{"save_id":"slot1","character":"fwiffo","encounter":"SPATHI_PLUTO"},
 "player_input":"Fwiffo, come with me.",
 "actions":[{"id":"join_us","text":"Come with me.","terminal":true}],
 "context":{"visits":0,"available_knowledge":["I_FWIFFO"]}}
```

## Tests

```bash
cd ai && python -m pytest tests/ -q
```

16 tests covering the failure modes the brief calls for: an unexported action never
mutates state, malformed output does not destabilise the loop, free conversation produces
no state transition, and both the LLM and TTS being unavailable remain survivable.

## Status

Implemented: protocol, validation, persona assembly, deterministic mock provider, stdio
service. Not yet implemented: the C-side hooks in `comm.c`, any real LLM provider, TTS,
and memory persistence.
