/* Reproduces a full conversational turn through the same spawn path the game
 * uses: child process, pipes, no console.
 *
 * The handshake alone does not exercise the model. This does, which is where
 * "stuck on transmitting" actually happens.
 */

#include <stdio.h>
#include <string.h>
#include "aiproc.h"

static void
onWait (void)
{
	static int ticks = 0;

	if (++ticks % 100 == 0)
		printf ("  ...waiting %d s\n", ticks / 100);
	fflush (stdout);
}

int
main (void)
{
	char err[256];
	char line[8192];
	const char *hello = "{\"type\":\"hello\",\"protocol\":1}\n";
	const char *turn =
		"{\"type\":\"converse\",\"id\":1,\"session_save_id\":\"slot0\","
		"\"session_character\":\"fwiffo\",\"session_encounter\":\"SPATHI_PLUTO\","
		"\"player_input\":\"Identify yourself\",\"actions\":["
		"{\"ref\":2,\"text\":\"Attention alien vessel: Identify yourself!\","
		"\"terminal\":false}],\"spoken_refs\":[1],\"visits\":0}\n";

	err[0] = '\0';
	if (!AiProc_Spawn ("ai", err, sizeof err))
	{
		printf ("SPAWN FAILED: %s\n", err);
		return 1;
	}
	printf ("spawn ok\n");

	if (!AiProc_Write (hello, strlen (hello))
			|| !AiProc_ReadLine (line, sizeof line, 30000, NULL))
	{
		printf ("HANDSHAKE FAILED\n");
		AiProc_Kill ();
		return 1;
	}
	printf ("handshake: %s\n", line);

	printf ("sending a real turn (this invokes the model)...\n");
	if (!AiProc_Write (turn, strlen (turn)))
	{
		printf ("WRITE FAILED\n");
		AiProc_Kill ();
		return 1;
	}

	if (!AiProc_ReadLine (line, sizeof line, 120000, onWait))
	{
		printf ("NO REPLY - this is the hang\n");
		AiProc_Kill ();
		return 1;
	}

	printf ("reply: %.400s\n", line);
	AiProc_Kill ();
	return 0;
}
