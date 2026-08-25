/*
 *  Client for the AI conversation sidecar.
 *
 *  Spawns the sidecar as a child process and exchanges newline-delimited
 *  JSON over its stdin/stdout. See docs/ai-architecture.md.
 *
 *  Actions are identified by the game's numeric RESPONSE_REF, because the
 *  phrase enum names exist only at compile time. The sidecar resolves those
 *  numbers to names using the same strings.h the game was built from.
 *
 *  Every entry point is safe to call when AI mode is off or the sidecar
 *  failed to start; the caller falls back to the original dialogue menu.
 */

#ifndef UQM_AI_AICONV_H
#define UQM_AI_AICONV_H

#include <stddef.h>
#include "libs/compiler.h"

#define AI_MAX_ACTIONS    24
#define AI_MAX_TEXT       2048
#define AI_MAX_INPUT      512

/* One action the encounter has exported this turn. */
typedef struct
{
	int ref;             /* RESPONSE_REF; what the game dispatches on */
	const char *text;    /* canonical wording, for the model's benefit */
	BOOLEAN terminal;    /* handler is ExitConversation */
} AI_ACTION;

/* The sidecar's reply, already validated against the actions we sent. */
typedef struct
{
	char spokenText[AI_MAX_TEXT];
	int action;          /* chosen RESPONSE_REF, or 0 for none */
	BOOLEAN hasAction;
} AI_REPLY;

/* Starts the sidecar and performs the handshake.
 * Returns FALSE if AI mode is disabled or the sidecar is unavailable, in
 * which case the game must use the original conversation system. */
BOOLEAN AiConv_Start (void);

/* Terminates the sidecar. Safe to call when it was never started. */
void AiConv_Stop (void);

/* TRUE once Start has succeeded and the sidecar is still usable. */
BOOLEAN AiConv_IsActive (void);

/* Runs one conversational turn.
 *
 * Returns FALSE on any failure - not started, write error, timeout,
 * malformed reply, or an action the encounter did not export. A FALSE
 * return leaves *reply untouched and means the caller should fall back;
 * it never indicates a partially applied result. */
BOOLEAN AiConv_Converse (const char *playerInput, const AI_ACTION *actions,
		int numActions, int visits, AI_REPLY *reply);

/* While suppressed, NPCPhrase() emits nothing.
 *
 * Generated prose replaces the canonical line rather than accompanying it, so
 * the encounter handler is dispatched with phrases muted: its state changes
 * still happen, but its authored text does not appear alongside the AI's. */
/* Installed by the game; called repeatedly while waiting for a reply so the
 * conversation screen keeps animating instead of appearing hung. */
void AiConv_SetWaitCallback (void (*fn) (void));

/* Records that the character has spoken a canonical phrase.
 *
 * These become the character's usable knowledge: he may repeat and elaborate
 * on what he has already said, but never volunteer canon he has not reached.
 * That keeps grounding and spoiler control on the same mechanism. */
/* Why the last AiConv_Converse failed, for showing the player.
 * Empty when nothing has failed. */
const char *AiConv_LastError (void);

void AiConv_NoteSpoken (int phraseRef);
void AiConv_ForgetSpoken (void);

void AiConv_SuppressPhrases (BOOLEAN suppress);
BOOLEAN AiConv_PhrasesSuppressed (void);

#endif /* UQM_AI_AICONV_H */
