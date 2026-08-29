/*
 *  Client for the AI conversation sidecar. See aiconv.h.
 *
 *  The OS-level transport lives in aiproc.c, which cannot share a
 *  translation unit with UQM headers because <windows.h> redefines BOOLEAN,
 *  RECT, POINT, SIZE, COORD and CONTEXT.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "aiconv.h"
#include "aijson.h"
#include "aiproc.h"
#include "aistate.h"
#include "libs/log.h"
#include "libs/uio.h"
#include "options.h"

extern uio_Repository *repository;

/* How long to wait for a reply before abandoning the turn. Generation is
 * slow, but a conversation that never returns is worse than one that falls
 * back to the menu. */
#define AI_REPLY_TIMEOUT_MS  90000

/* Must hold the whole request: a long player message plus every action
 * the encounter exported, all JSON-escaped. */
#define AI_LINE_MAX          32768

/* Where the sidecar lives, relative to the game's working directory. */
#define AI_WORKING_DIR       "ai"

static BOOLEAN aiActive = FALSE;
static BOOLEAN aiSuppressPhrases = FALSE;
static int aiNextRequestId = 1;
static AiProc_WaitFn aiWaitFn = NULL;

/* Sized for the largest character rather than for Fwiffo. Starbase has 116
 * NPCPhrase call sites and a long visit will pass 64, after which the
 * character quietly stops being grounded in what he just said - a failure
 * with no symptom except gradually worse answers. */
#define AI_MAX_SPOKEN 256
static int aiSpoken[AI_MAX_SPOKEN];
static int aiSpokenCount = 0;
static BOOLEAN aiSpokenFull = FALSE;

/* Actions the player has dispatched this conversation. */
#define AI_MAX_DISPATCHED 64
static int aiDispatched[AI_MAX_DISPATCHED];
static int aiDispatchedCount = 0;

/* The authored answer produced by the last dispatch. */
static char aiCaptured[AI_MAX_AUTHORED];
static size_t aiCapturedLen = 0;

/* Kept so a failure can be shown in the conversation rather than only logged;
 * an unexplained silence reads as the game hanging. */
static char aiLastError[256] = "";

const char *
AiConv_LastError (void)
{
	return aiLastError;
}

static void
setLastError (const char *message)
{
	aiLastError[0] = '\0';
	if (message != NULL)
		strncpy (aiLastError, message, sizeof (aiLastError) - 1);
}

void
AiConv_NoteSpoken (int phraseRef)
{
	int i;

	if (phraseRef <= 0)
		return;
	if (aiSpokenCount >= AI_MAX_SPOKEN)
	{	/* Once, not per phrase: this would otherwise fill the log. */
		if (!aiSpokenFull)
		{
			log_add (log_Warning, "AI: spoken-phrase buffer full at %d; the "
					"character is no longer grounded in all he has said",
					AI_MAX_SPOKEN);
			aiSpokenFull = TRUE;
		}
		return;
	}
	for (i = 0; i < aiSpokenCount; ++i)
	{
		if (aiSpoken[i] == phraseRef)
			return;
	}
	aiSpoken[aiSpokenCount++] = phraseRef;
}

void
AiConv_ForgetSpoken (void)
{
	aiSpokenCount = 0;
	aiSpokenFull = FALSE;
}

void
AiConv_NoteDispatched (int responseRef)
{
	int i;

	if (responseRef <= 0 || aiDispatchedCount >= AI_MAX_DISPATCHED)
		return;
	for (i = 0; i < aiDispatchedCount; ++i)
	{
		if (aiDispatched[i] == responseRef)
			return;
	}
	aiDispatched[aiDispatchedCount++] = responseRef;
}

BOOLEAN
AiConv_WasDispatched (int responseRef)
{
	int i;

	for (i = 0; i < aiDispatchedCount; ++i)
	{
		if (aiDispatched[i] == responseRef)
			return TRUE;
	}
	return FALSE;
}

void
AiConv_ForgetDispatched (void)
{
	aiDispatchedCount = 0;
}

void
AiConv_SetWaitCallback (void (*fn) (void))
{
	aiWaitFn = (AiProc_WaitFn)fn;
}

void
AiConv_SuppressPhrases (BOOLEAN suppress)
{
	aiSuppressPhrases = suppress;
}

BOOLEAN
AiConv_PhrasesSuppressed (void)
{
	return aiSuppressPhrases;
}

void
AiConv_ClearCaptured (void)
{
	aiCaptured[0] = '\0';
	aiCapturedLen = 0;
}

void
AiConv_CaptureText (const char *text)
{
	size_t len;

	if (text == NULL || text[0] == '\0')
		return;

	/* Separate consecutive phrases, which are separate beats of one answer. */
	if (aiCapturedLen > 0 && aiCapturedLen + 2 < sizeof (aiCaptured))
	{
		aiCaptured[aiCapturedLen++] = '\n';
		aiCaptured[aiCapturedLen] = '\0';
	}

	len = strlen (text);
	if (aiCapturedLen + len >= sizeof (aiCaptured))
		len = sizeof (aiCaptured) - aiCapturedLen - 1;
	memcpy (aiCaptured + aiCapturedLen, text, len);
	aiCapturedLen += len;
	aiCaptured[aiCapturedLen] = '\0';
}

const char *
AiConv_CapturedText (void)
{
	return aiCaptured;
}

/* ---- messaging -------------------------------------------------------- */

static BOOLEAN readMessage (char *buf, size_t cap, AiJsonObject *obj);

static BOOLEAN
sendLine (const char *line)
{
	if (!AiProc_Write (line, strlen (line)))
		return FALSE;
	return (BOOLEAN)AiProc_Write ("\n", 1);
}

/* Makes the sidecar's scratch directory visible to the track player.
 *
 * The sidecar owns the directory - it creates it, writes into it and removes
 * it on exit - and reports the native path at handshake. Mounting it at the
 * repository root is what lets SpliceTrack load generated speech: clip names
 * resolve through contentDir, which is that same root, so "uqmai/line-7.wav"
 * needs no new decoder path and no change to the track player.
 *
 * Failure is not fatal. Speech falls back to the borrowed carrier clip, which
 * is how every conversation has worked so far. */
static void
mountVoiceDir (const char *nativePath)
{
	static uio_AutoMount *autoMount[] = { NULL };
	uio_MountHandle *handle;

	if (nativePath == NULL || nativePath[0] == '\0')
	{
		log_add (log_Info, "AI: sidecar reported no voice directory;"
				" speech will use the carrier clip");
		return;
	}

	handle = uio_mountDir (repository, "/" AI_VOICE_MOUNT "/",
			uio_FSTYPE_STDIO, NULL, NULL, nativePath, autoMount,
			uio_MOUNT_TOP, NULL);
	if (handle == NULL)
	{
		log_add (log_Warning, "AI: could not mount voice dir '%s'",
				nativePath);
		return;
	}

	log_add (log_Info, "AI: voice dir '%s' mounted at /%s", nativePath,
			AI_VOICE_MOUNT);
}

static BOOLEAN
handshake (void)
{
	char line[AI_LINE_MAX];
	AiJsonObject obj;
	const char *type;
	const char *provider;

	if (!sendLine ("{\"type\":\"hello\",\"protocol\":1}"))
		return FALSE;
	if (!readMessage (line, sizeof line, &obj))
	{
		log_add (log_Warning, "AI: no handshake reply from sidecar");
		setLastError ("The AI service did not respond. Is Python installed "
				"and on PATH?");
		return FALSE;
	}

	type = AiJson_GetString (&obj, "type");
	if (type != NULL && strcmp (type, "fatal") == 0)
	{	/* The service ran its own startup checks and refused to start.
		 * Carry its reason out so the player is told what to fix, rather
		 * than being shown a generic failure. */
		const char *why = AiJson_GetString (&obj, "message");

		setLastError (why != NULL ? why : "The AI service failed its checks.");
		log_add (log_Warning, "AI: %s", AiConv_LastError ());
		return FALSE;
	}
	if (type == NULL || strcmp (type, "ready") != 0)
	{
		log_add (log_Warning, "AI: sidecar did not report ready");
		setLastError ("The AI service did not start correctly.");
		return FALSE;
	}

	provider = AiJson_GetString (&obj, "provider");
	log_add (log_Info, "AI: sidecar ready (provider %s)",
			provider != NULL ? provider : "unknown");

	mountVoiceDir (AiJson_GetString (&obj, "voice_dir"));
	return TRUE;
}

BOOLEAN
AiConv_Start (void)
{
	char err[256];

	if (aiActive)
		return TRUE;

	err[0] = '\0';
	/* Speech is opt-in. It costs a multi-gigabyte environment, several
	 * gigabytes of video memory and tens of seconds per line, none of which
	 * a player who only wants to talk should have to pay. */
	if (!AiProc_Spawn (AI_WORKING_DIR,
			optAiVoice == OPTVAL_ENABLED ? "chatterbox" : "none",
			err, sizeof err))
	{
		log_add (log_Warning, "AI: could not start sidecar: %s", err);
		return FALSE;
	}

	if (!handshake ())
	{
		AiProc_Kill ();
		return FALSE;
	}

	aiNextRequestId = 1;
	aiActive = TRUE;
	return TRUE;
}

void
AiConv_Stop (void)
{
	if (!aiActive)
		return;
	AiProc_Kill ();
	aiActive = FALSE;
}

BOOLEAN
AiConv_IsActive (void)
{
	return aiActive;
}

/* Reads the generated speech filename, if the sidecar produced one.
 *
 * Only a bare filename is accepted. Anything carrying a path separator is
 * refused outright rather than sanitised: a name that needs sanitising is a
 * name we did not expect, and this is the one field where model output could
 * otherwise reach the filesystem. */
static void
readAudioClip (const AiJsonObject *obj, AI_REPLY *reply)
{
	const char *name = AiJson_GetString (obj, "audio_file");

	reply->audioClip[0] = '\0';
	if (name == NULL || name[0] == '\0')
		return;

	if (strpbrk (name, "/\\:") != NULL)
	{
		log_add (log_Warning, "AI: refusing audio filename '%s'", name);
		return;
	}

	snprintf (reply->audioClip, sizeof (reply->audioClip), "%s/%s",
			AI_VOICE_MOUNT, name);
}

/* Sends one request and parses the reply, which must be of the expected type.
 *
 * buf carries the request in and the reply out; the two never overlap in
 * time, and one buffer of this size is already the largest thing on the
 * stack here. */
/* Reads one message, passing the sidecar's own diagnostics into the log.
 *
 * The sidecar reports on itself over the protocol rather than on stderr,
 * because inherited stderr does not survive the way the game is launched and
 * everything it printed was being dropped. Those lines can arrive at any
 * point - the voice model warms up on its own thread - so any read may see
 * them before the message it is actually waiting for. */
static BOOLEAN
readMessage (char *buf, size_t cap, AiJsonObject *obj)
{
	for (;;)
	{
		const char *type;

		if (!AiProc_ReadLine (buf, cap, AI_REPLY_TIMEOUT_MS, aiWaitFn))
			return FALSE;

		if (!AiJson_Parse (buf, obj))
		{
			log_add (log_Warning, "AI: malformed reply");
			return FALSE;
		}

		type = AiJson_GetString (obj, "type");
		if (type == NULL || strcmp (type, "log") != 0)
			return TRUE;

		{
			const char *message = AiJson_GetString (obj, "message");

			log_add (log_Info, "AI: %s",
					message != NULL ? message : "(empty log line)");
		}
	}
}

static BOOLEAN
exchange (char *buf, size_t cap, const char *expectedType, AiJsonObject *obj)
{
	const char *type;

	if (!sendLine (buf) || !readMessage (buf, cap, obj))
	{
		log_add (log_Warning, "AI: sidecar stopped responding");
		setLastError ("The AI service stopped responding.");
		aiActive = FALSE;
		return FALSE;
	}

	type = AiJson_GetString (obj, "type");
	if (type == NULL || strcmp (type, expectedType) != 0)
	{
		const char *message = AiJson_GetString (obj, "message");

		log_add (log_Warning, "AI: %s",
				message != NULL ? message : "unexpected reply");
		setLastError (message != NULL ? message : "The AI service failed.");
		return FALSE;
	}
	return TRUE;
}

/* Opens a request and writes the fields every request type carries.
 *
 * Session identity is carried from the first protocol version, before memory
 * exists, because retrofitting it later is far more expensive. */
static void
beginRequest (AiJsonWriter *w, char *buf, size_t cap, const char *type,
		int requestId, const char *playerInput)
{
	AiJson_InitWriter (w, buf, cap);
	AiJson_BeginObject (w);
	AiJson_WriteString (w, "type", type);
	AiJson_WriteInt (w, "id", requestId);

	{	/* The dialogue resource name is the character's identity, and it is
		 * already sitting in CommData - see AiState_CharacterId. NULL only
		 * outside a conversation, where the sidecar answers a protocol error
		 * and the game falls back to its own menu. */
		const char *character = AiState_CharacterId ();
		int day = 0, month = 0, year = 0;
		char date[16];

		{	/* Memory is keyed on this. A game that has never been saved or
			 * loaded has no slot, and gets one that cannot collide with a
			 * real one. */
			int slot = AiState_SaveSlot ();
			char saveId[24];

			if (slot >= 0)
				snprintf (saveId, sizeof saveId, "slot%d", slot);
			else
				strcpy (saveId, "unsaved");
			AiJson_WriteString (w, "session_save_id", saveId);
		}
		AiJson_WriteString (w, "session_character",
				character != NULL ? character : "");
		AiJson_WriteString (w, "session_encounter",
				character != NULL ? character : "");

		AiState_Date (&day, &month, &year);
		snprintf (date, sizeof date, "%04d-%02d-%02d", year, month, day);
		AiJson_WriteString (w, "game_date", date);
	}

	AiJson_WriteString (w, "player_input", playerInput);
}

/* Where the story has got to, as NAME=VALUE strings.
 *
 * An array of strings rather than an object because the writer has no keyed
 * nested-object support, and 453 top-level fields would be grotesque. Only
 * non-zero flags are sent: absent already means zero to the sidecar, which is
 * getGameStateUint's own contract for an unset property. */
static void
writeState (AiJsonWriter *w)
{
	AI_STATE_ENTRY entries[AI_MAX_STATE];
	char pair[AIJSON_MAX_VALUE];
	int count;
	int i;

	count = AiState_Collect (entries, AI_MAX_STATE);
	if (count >= AI_MAX_STATE)
		log_add (log_Warning, "AI: state buffer full at %d entries; some "
				"knowledge will not unlock", count);

	{	/* The operator-visible surface for auditing a wrong answer: who is
		 * speaking, when, and how much of the world they were told about.
		 * See docs/character-knowledge-model.md section 6. */
		const char *who = AiState_CharacterId ();
		int day = 0, month = 0, year = 0;

		AiState_Date (&day, &month, &year);
		log_add (log_Info, "AI: character=%s date=%04d-%02d-%02d state=%d flags",
				who != NULL ? who : "(none)", year, month, day, count);
	}

	AiJson_BeginArray (w, "state");
	for (i = 0; i < count; ++i)
	{
		AiJson_WriteRawElement (w);
		snprintf (pair, sizeof pair, "%s=%u",
				entries[i].name, entries[i].value);
		AiJson_WriteString (w, NULL, pair);
	}
	AiJson_EndArray (w);
}

static void
writeActions (AiJsonWriter *w, const AI_ACTION *actions, int numActions)
{
	int i;

	AiJson_BeginArray (w, "actions");
	for (i = 0; i < numActions; ++i)
	{
		AiJson_WriteRawElement (w);
		AiJson_BeginObject (w);
		AiJson_WriteInt (w, "ref", actions[i].ref);
		AiJson_WriteString (w, "text", actions[i].text);
		AiJson_WriteBool (w, "terminal", actions[i].terminal);
		AiJson_WriteInt (w, "flow", actions[i].flow);
		AiJson_WriteBool (w, "repeated", actions[i].repeated);
		AiJson_EndObject (w);
	}
	AiJson_EndArray (w);
}

/* What he has already said this conversation, which is what he is allowed to
 * draw on: he may repeat and elaborate, never volunteer canon he has not
 * reached. */
static void
writeSpokenRefs (AiJsonWriter *w)
{
	int i;

	AiJson_BeginArray (w, "spoken_refs");
	for (i = 0; i < aiSpokenCount; ++i)
	{
		AiJson_WriteRawElement (w);
		AiJson_WriteInt (w, NULL, aiSpoken[i]);
	}
	AiJson_EndArray (w);
}

void
AiConv_EndEncounter (void)
{
	char line[AI_LINE_MAX];
	AiJsonWriter w;
	const char *character = AiState_CharacterId ();

	if (!aiActive || character == NULL || character[0] == '\0')
		return;

	beginRequest (&w, line, sizeof line, "encounter_end", 0, "");
	AiJson_EndObject (&w);

	if (!AiJson_WriterOk (&w) || !sendLine (line))
		log_add (log_Warning, "AI: could not tell the sidecar the "
				"conversation ended; it will not be remembered");
}

/* Builds the request separately, so a serialisation overflow is caught
 * before anything reaches the pipe. */
static BOOLEAN
buildRequest (char *buf, size_t cap, int requestId, const char *playerInput,
		const AI_ACTION *actions, int numActions, int visits)
{
	AiJsonWriter w;
	int i;

	beginRequest (&w, buf, cap, "converse", requestId, playerInput);
	writeActions (&w, actions, numActions);
	writeSpokenRefs (&w);
	writeState (&w);

	AiJson_WriteInt (&w, "visits", visits);
	AiJson_EndObject (&w);

	return AiJson_WriterOk (&w);
}

static BOOLEAN
buildNarrateRequest (char *buf, size_t cap, int requestId,
		const char *playerInput, const char *authoredText)
{
	AiJsonWriter w;

	beginRequest (&w, buf, cap, "narrate", requestId, playerInput);
	AiJson_WriteString (&w, "authored_text", authoredText);
	writeSpokenRefs (&w);
	writeState (&w);
	AiJson_EndObject (&w);

	return AiJson_WriterOk (&w);
}

BOOLEAN
AiConv_Narrate (const char *playerInput, const char *authoredText,
		AI_REPLY *reply)
{
	char line[AI_LINE_MAX];
	AiJsonObject obj;
	const char *spoken;

	if (!aiActive || authoredText == NULL || authoredText[0] == '\0')
		return FALSE;

	setLastError (NULL);

	if (!buildNarrateRequest (line, sizeof line, aiNextRequestId++,
			playerInput, authoredText))
	{
		log_add (log_Warning, "AI: narrate request too large to serialise");
		return FALSE;
	}

	if (!exchange (line, sizeof line, "narrate", &obj))
		return FALSE;

	spoken = AiJson_GetString (&obj, "spoken_text");
	if (spoken == NULL || spoken[0] == '\0')
	{
		log_add (log_Warning, "AI: narrate reply had no text");
		return FALSE;
	}

	/* No action is read here, and none is accepted: the action has already
	 * been dispatched and its outcome is what this call is describing. */
	memset (reply, 0, sizeof (*reply));
	strncpy (reply->spokenText, spoken, AI_MAX_TEXT - 1);
	readAudioClip (&obj, reply);
	return TRUE;
}

BOOLEAN
AiConv_Converse (const char *playerInput, const AI_ACTION *actions,
		int numActions, int visits, AI_REPLY *reply)
{
	char line[AI_LINE_MAX];
	AiJsonObject obj;
	const char *spoken;
	int action = 0;
	int i;

	if (!aiActive || numActions <= 0)
		return FALSE;

	setLastError (NULL);

	if (!buildRequest (line, sizeof line, aiNextRequestId++, playerInput,
			actions, numActions, visits))
	{
		log_add (log_Warning, "AI: request too large to serialise");
		return FALSE;
	}

	if (!exchange (line, sizeof line, "converse", &obj))
		return FALSE;

	spoken = AiJson_GetString (&obj, "spoken_text");
	if (spoken == NULL || spoken[0] == '\0')
	{
		log_add (log_Warning, "AI: reply had no text");
		return FALSE;
	}

	/* The authoritative check: an action must be one we exported this turn.
	 * Anything else is discarded and the turn proceeds as pure conversation,
	 * because losing a transition is always safer than inventing one. */
	if (AiJson_GetInt (&obj, "action", &action) && action != 0)
	{
		BOOLEAN permitted = FALSE;

		for (i = 0; i < numActions; ++i)
		{
			if (actions[i].ref == action)
			{
				permitted = TRUE;
				break;
			}
		}

		if (!permitted)
		{
			log_add (log_Warning,
					"AI: rejected action %d - not offered this turn", action);
			action = 0;
		}
	}

	memset (reply, 0, sizeof (*reply));
	strncpy (reply->spokenText, spoken, AI_MAX_TEXT - 1);
	reply->action = action;
	reply->hasAction = (BOOLEAN)(action != 0);
	readAudioClip (&obj, reply);
	return TRUE;
}
