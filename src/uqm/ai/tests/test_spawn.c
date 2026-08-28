/* Verifies the sidecar can be spawned regardless of the current directory.
 *
 * Built into the game's own folder so the executable-relative resolution has
 * the same layout it does in play, then run from somewhere else entirely.
 */

#include <stdio.h>
#include <string.h>
#include "aiproc.h"

int
main (void)
{
	char err[256];
	char line[8192];

	err[0] = '\0';
	if (!AiProc_Spawn ("ai", "none", err, sizeof err))
	{
		printf ("SPAWN FAILED: %s\n", err);
		return 1;
	}
	printf ("spawn ok\n");

	{
		const char *hello = "{\"type\":\"hello\",\"protocol\":1}\n";

		if (!AiProc_Write (hello, strlen (hello)))
		{
			printf ("WRITE FAILED\n");
			AiProc_Kill ();
			return 1;
		}
	}

	if (!AiProc_ReadLine (line, sizeof line, 30000, NULL))
	{
		printf ("NO REPLY\n");
		AiProc_Kill ();
		return 1;
	}

	printf ("reply: %s\n", line);
	AiProc_Kill ();
	return 0;
}
