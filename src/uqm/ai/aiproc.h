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
 * Returns 1 on success. On failure, errBuf receives a short diagnostic. */
int AiProc_Spawn (const char *workingDir, char *errBuf, size_t errCap);

/* Terminates the sidecar. Safe when nothing was started. */
void AiProc_Kill (void);

/* Writes exactly len bytes. Returns 1 on success. */
int AiProc_Write (const char *data, size_t len);

/* Reads one newline-terminated line, waiting at most timeoutMs.
 * The newline is not stored. Returns 1 on success, 0 on timeout or EOF. */
int AiProc_ReadLine (char *buf, size_t cap, int timeoutMs);

#endif /* UQM_AI_AIPROC_H */
