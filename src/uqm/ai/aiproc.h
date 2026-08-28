/*
 *  Child-process transport for the AI sidecar.
 *
 *  Deliberately isolated from the rest of the game: this is the only place
 *  that includes <windows.h>, which defines BOOLEAN, RECT, POINT, SIZE,
 *  COORD and CONTEXT and therefore cannot coexist with UQM's headers in one
 *  translation unit. Nothing here uses a UQM type, so the two never meet.
 *
 *  All functions return int (1 success / 0 failure) rather than BOOLEAN for
 *  the same reason.
 */

#ifndef UQM_AI_AIPROC_H
#define UQM_AI_AIPROC_H

#include <stddef.h>

/* Launches the sidecar with the given working directory.
 *
 * ttsKind selects the speech provider ("none", "canned", "chatterbox"). It is
 * passed as a plain string because this file cannot see the game's options.
 *
 * Returns 1 on success. On failure, errBuf receives a short diagnostic. */
int AiProc_Spawn (const char *workingDir, const char *ttsKind,
		char *errBuf, size_t errCap);

/* Terminates the sidecar. Safe when nothing was started. */
void AiProc_Kill (void);

/* Writes exactly len bytes. Returns 1 on success. */
int AiProc_Write (const char *data, size_t len);

/* Called repeatedly while blocked waiting for a reply.
 *
 * Generation takes tens of seconds, during which the game would otherwise
 * be frozen. The callback lets the caller keep the screen alive without
 * this file needing to know anything about UQM. */
typedef void (*AiProc_WaitFn) (void);

/* Reads one newline-terminated line, waiting at most timeoutMs.
 * The newline is not stored. onWait may be NULL.
 * Returns 1 on success, 0 on timeout or EOF. */
int AiProc_ReadLine (char *buf, size_t cap, int timeoutMs,
		AiProc_WaitFn onWait);

#endif /* UQM_AI_AIPROC_H */
