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
#include "libs/log.h"
#include "libs/uio.h"

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

#define AI_MAX_SPOKEN 64
static int aiSpoken[AI_MAX_SPOKEN];
static int aiSpokenCount = 0;

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

	if (phraseRef <= 0 || aiSpokenCount >= AI_MAX_SPOKEN)
		return;
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
	if (!AiProc_ReadLine (line, sizeof line, AI_REPLY_TIMEOUT_MS, aiWaitFn))
	{
		log_add (log_Warning, "AI: no handshake reply from sidecar");
		setLastError ("The AI service did not respond. Is Python installed "
				"and on PATH?");
		return FALSE;
	}
	if (!AiJson_Parse (line, &obj))
	{
		log_add (log_Warning, "AI: unparseable handshake reply");
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
	if (!AiProc_Spawn (AI_WORKING_DIR, err, sizeof err))
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
static BOOLEAN
exchange (char *buf, size_t cap, const char *expectedType, AiJsonObject *obj)
{
	const char *type;

	if (!sendLine (buf)
			|| !AiProc_ReadLine (buf, cap, AI_REPLY_TIMEOUT_MS, aiWaitFn))
	{
		log_add (log_Warning, "AI: sidecar stopped responding");
		setLastError ("The AI service stopped responding.");
		aiActive = FALSE;
		return FALSE;
	}

	if (!AiJson_Parse (buf, obj))
	{
		log_add (log_Warning, "AI: malformed reply");
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

	AiJson_WriteString (w, "session_save_id", "slot0");
	AiJson_WriteString (w, "session_character", "fwiffo");
	AiJson_WriteString (w, "session_encounter", "SPATHI_PLUTO");

	AiJson_WriteString (w, "player_input", playerInput);
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
