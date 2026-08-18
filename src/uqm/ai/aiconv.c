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

/* How long to wait for a reply before abandoning the turn. Generation is
 * slow, but a conversation that never returns is worse than one that falls
 * back to the menu. */
#define AI_REPLY_TIMEOUT_MS  90000

#define AI_LINE_MAX          8192

/* Where the sidecar lives, relative to the game's working directory. */
#define AI_WORKING_DIR       "ai"

static BOOLEAN aiActive = FALSE;
static BOOLEAN aiSuppressPhrases = FALSE;
static int aiNextRequestId = 1;
static AiProc_WaitFn aiWaitFn = NULL;

#define AI_MAX_SPOKEN 64
static int aiSpoken[AI_MAX_SPOKEN];
static int aiSpokenCount = 0;

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

/* ---- messaging -------------------------------------------------------- */

static BOOLEAN
sendLine (const char *line)
{
	if (!AiProc_Write (line, strlen (line)))
		return FALSE;
	return (BOOLEAN)AiProc_Write ("\n", 1);
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
		return FALSE;
	}
	if (!AiJson_Parse (line, &obj))
	{
		log_add (log_Warning, "AI: unparseable handshake reply");
		return FALSE;
	}

	type = AiJson_GetString (&obj, "type");
	if (type == NULL || strcmp (type, "ready") != 0)
	{
		log_add (log_Warning, "AI: sidecar did not report ready");
		return FALSE;
	}

	provider = AiJson_GetString (&obj, "provider");
	log_add (log_Info, "AI: sidecar ready (provider %s)",
			provider != NULL ? provider : "unknown");
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

/* Builds the request separately, so a serialisation overflow is caught
 * before anything reaches the pipe. */
static BOOLEAN
buildRequest (char *buf, size_t cap, int requestId, const char *playerInput,
		const AI_ACTION *actions, int numActions, int visits)
{
	AiJsonWriter w;
	int i;

	AiJson_InitWriter (&w, buf, cap);
	AiJson_BeginObject (&w);
	AiJson_WriteString (&w, "type", "converse");
	AiJson_WriteInt (&w, "id", requestId);

	/* Session identity is carried from the first protocol version, before
	 * memory exists, because retrofitting it later is far more expensive. */
	AiJson_WriteString (&w, "session_save_id", "slot0");
	AiJson_WriteString (&w, "session_character", "fwiffo");
	AiJson_WriteString (&w, "session_encounter", "SPATHI_PLUTO");

	AiJson_WriteString (&w, "player_input", playerInput);

	AiJson_BeginArray (&w, "actions");
	for (i = 0; i < numActions; ++i)
	{
		AiJson_WriteRawElement (&w);
		AiJson_BeginObject (&w);
		AiJson_WriteInt (&w, "ref", actions[i].ref);
		AiJson_WriteString (&w, "text", actions[i].text);
		AiJson_WriteBool (&w, "terminal", actions[i].terminal);
		AiJson_EndObject (&w);
	}
	AiJson_EndArray (&w);

	/* What he has already said this conversation, which is what he is
	 * allowed to draw on. */
	AiJson_BeginArray (&w, "spoken_refs");
	for (i = 0; i < aiSpokenCount; ++i)
	{
		AiJson_WriteRawElement (&w);
		AiJson_WriteInt (&w, NULL, aiSpoken[i]);
	}
	AiJson_EndArray (&w);

	AiJson_WriteInt (&w, "visits", visits);
	AiJson_EndObject (&w);

	return AiJson_WriterOk (&w);
}

BOOLEAN
AiConv_Converse (const char *playerInput, const AI_ACTION *actions,
		int numActions, int visits, AI_REPLY *reply)
{
	char line[AI_LINE_MAX];
	AiJsonObject obj;
	const char *type;
	const char *spoken;
	int action = 0;
	int i;

	if (!aiActive || numActions <= 0)
		return FALSE;

	if (!buildRequest (line, sizeof line, aiNextRequestId++, playerInput,
			actions, numActions, visits))
	{
		log_add (log_Warning, "AI: request too large to serialise");
		return FALSE;
	}

	if (!sendLine (line)
			|| !AiProc_ReadLine (line, sizeof line, AI_REPLY_TIMEOUT_MS, aiWaitFn))
	{
		log_add (log_Warning, "AI: sidecar stopped responding");
		aiActive = FALSE;
		return FALSE;
	}

	if (!AiJson_Parse (line, &obj))
	{
		log_add (log_Warning, "AI: malformed reply");
		return FALSE;
	}

	type = AiJson_GetString (&obj, "type");
	if (type == NULL || strcmp (type, "converse") != 0)
	{
		const char *message = AiJson_GetString (&obj, "message");

		log_add (log_Warning, "AI: %s",
				message != NULL ? message : "unexpected reply");
		return FALSE;
	}

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
	return TRUE;
}
