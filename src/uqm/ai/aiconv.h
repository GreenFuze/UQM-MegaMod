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

/* Room for every named game state flag plus the derived SIS_ values. The
 * table in save.c holds 453; only the non-zero ones are ever sent, so this is
 * a ceiling rather than a working size. */
#define AI_MAX_STATE      512

#define AI_MAX_ACTIONS    24
#define AI_MAX_TEXT       2048
/* One dispatch can emit several canonical phrases in a row, and all of them
 * together are the authored answer. */
#define AI_MAX_AUTHORED   4096
/* Players write paragraphs, not commands. A cap that stops them
 * mid-argument is worse than a longer request. */
#define AI_MAX_INPUT      2048

/* Where an action leads, relative to the point the conversation is at now.
 *
 * A response whose handler is the same function that registered it returns to
 * exactly this point: it cannot end the encounter and cannot commit the
 * player to anything, because the node simply re-registers itself. One with a
 * different handler is a departure - recruitment, combat, or a new topic.
 *
 * This is how the model learns which "come with us" is real. Fwiffo's join_us
 * is wired to SpathiOnPluto while the question chain is unspent, and only to
 * ExitConversation once it is spent; the difference is precisely whether he
 * refuses. Comparing against ExitConversation by name is impossible - it is
 * static in every race's comm file - but comparing against the current node
 * costs nothing and needs nothing per race. */
#define AI_FLOW_UNKNOWN    0  /* nothing has been dispatched yet */
#define AI_FLOW_SAME_NODE  1  /* returns here; nothing new opens up */
#define AI_FLOW_DEPARTS    2  /* leads elsewhere; may end the conversation */

/* One action the encounter has exported this turn. */
typedef struct
{
	int ref;             /* RESPONSE_REF; what the game dispatches on */
	const char *text;    /* canonical wording, for the model's benefit */
	/* Whether the handler is the encounter's ExitConversation.
	 *
	 * Currently always FALSE, and cannot be computed: ExitConversation is a
	 * static function defined separately in every race's comm file, so there
	 * is no symbol comm.c could compare response_func against. The field is
	 * kept because the outcome now reaches the model a better way - see
	 * AiConv_Narrate - and a per-race registry would buy nothing. */
	BOOLEAN terminal;
	int flow;            /* one of AI_FLOW_* */
	/* Dispatched earlier this conversation and still on offer, which proves
	 * the encounter did not consume it: choosing it again changes nothing.
	 * See AiConv_NoteDispatched. */
	BOOLEAN repeated;
} AI_ACTION;

/* Where generated speech is mounted inside the game's virtual filesystem.
 *
 * SpliceTrack resolves clip names through contentDir, which is the repository
 * root, so a directory mounted here is addressable as an ordinary clip path
 * with no change to the track player. */
#define AI_VOICE_MOUNT    "uqmai"

/* The sidecar's reply, already validated against the actions we sent. */
typedef struct
{
	char spokenText[AI_MAX_TEXT];
	int action;          /* chosen RESPONSE_REF, or 0 for none */
	BOOLEAN hasAction;
	/* Clip path for the generated speech, ready to hand to SpliceTrack, or
	 * empty when the sidecar produced no audio.
	 *
	 * The sidecar sends a BARE FILENAME and this is built from it. A model
	 * that emitted "../../../etc/passwd" would therefore be naming a file
	 * inside the voice directory with a silly name, not escaping it. */
	char audioClip[128];
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

/* Renders the encounter's own answer in the character's voice.
 *
 * Dispatching an action is what decides the outcome, and the handler says so
 * in authored text: WONT_JOIN when he refuses, WILL_JOIN when he does not.
 * Generating prose BEFORE that dispatch let the model agree to things the
 * encounter then refused, which the player saw as a promise followed by
 * nothing. So the authored answer is captured during dispatch and passed
 * here, and the model is only allowed to reword it.
 *
 * Nothing is chosen here and no action list is sent. An earlier revision
 * passed one so he could nudge the captain towards a topic he had not raised
 * yet; it worked, and it was wrong. Being herded back onto the authored order
 * is precisely what free text exists to escape.
 *
 * Returns FALSE on any failure, in which case the caller must speak the
 * authored text verbatim: the game's own words are always a correct answer,
 * and only the phrasing is lost. */
BOOLEAN AiConv_Narrate (const char *playerInput, const char *authoredText,
		AI_REPLY *reply);

/* While suppressed, NPCPhrase() speaks nothing and hands its text to
 * AiConv_CaptureText instead.
 *
 * Generated prose replaces the canonical delivery rather than accompanying
 * it, so the encounter handler is dispatched with phrases muted: its state
 * changes still happen and its words are still recorded, but the player hears
 * one line rather than two contradicting ones. */
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

/* Records that the player has dispatched an action.
 *
 * A ref that is STILL on offer after being dispatched was not consumed by
 * the encounter: choosing it again gives the same kind of answer and moves
 * nothing. join_us behaves that way until its question chain is spent, while
 * what_doing_on_pluto_1 is disabled the moment it is used and never returns.
 * Structure alone cannot tell those apart - both are wired back to the same
 * handler - so this is observed instead of guessed. */
void AiConv_NoteDispatched (int responseRef);
BOOLEAN AiConv_WasDispatched (int responseRef);
void AiConv_ForgetDispatched (void);

void AiConv_SuppressPhrases (BOOLEAN suppress);
BOOLEAN AiConv_PhrasesSuppressed (void);

/* Records one suppressed canonical line. Successive calls accumulate, since
 * a single dispatch may emit several phrases in sequence. */
void AiConv_CaptureText (const char *text);

/* Everything captured since the last AiConv_ClearCaptured, or "" if the
 * handler produced no dialogue at all. Never NULL. */
const char *AiConv_CapturedText (void);

void AiConv_ClearCaptured (void);

#endif /* UQM_AI_AICONV_H */
