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
