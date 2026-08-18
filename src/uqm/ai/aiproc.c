/*
 *  Child-process transport for the AI sidecar. See aiproc.h.
 *
 *  This file must not include any UQM header.
 */

#include <stdio.h>
#include <string.h>

#include "aiproc.h"

#ifdef _WIN32

#include <windows.h>

static HANDLE procHandle = NULL;
static HANDLE childStdinWr = NULL;
static HANDLE childStdoutRd = NULL;

int
AiProc_Spawn (const char *workingDir, char *errBuf, size_t errCap)
{
	SECURITY_ATTRIBUTES sa;
	STARTUPINFOA si;
	PROCESS_INFORMATION pi;
	HANDLE stdinRd = NULL, stdinWr = NULL;
	HANDLE stdoutRd = NULL, stdoutWr = NULL;
	char cmdline[] = "python -m uqm_ai --provider claude";

	sa.nLength = sizeof (SECURITY_ATTRIBUTES);
	sa.bInheritHandle = TRUE;
	sa.lpSecurityDescriptor = NULL;

	if (!CreatePipe (&stdoutRd, &stdoutWr, &sa, 0))
	{
		snprintf (errBuf, errCap, "could not create stdout pipe");
		return 0;
	}
	if (!CreatePipe (&stdinRd, &stdinWr, &sa, 0))
	{
		snprintf (errBuf, errCap, "could not create stdin pipe");
		CloseHandle (stdoutRd);
		CloseHandle (stdoutWr);
		return 0;
	}

	/* Our ends must not be inherited, or the child holds them open and we
	 * never observe EOF when it exits. */
	SetHandleInformation (stdoutRd, HANDLE_FLAG_INHERIT, 0);
	SetHandleInformation (stdinWr, HANDLE_FLAG_INHERIT, 0);

	memset (&si, 0, sizeof (si));
	si.cb = sizeof (si);
	si.hStdInput = stdinRd;
	si.hStdOutput = stdoutWr;
	si.hStdError = GetStdHandle (STD_ERROR_HANDLE);
	si.dwFlags = STARTF_USESTDHANDLES;

	memset (&pi, 0, sizeof (pi));

	if (!CreateProcessA (NULL, cmdline, NULL, NULL, TRUE,
			CREATE_NO_WINDOW, NULL, workingDir, &si, &pi))
	{
		snprintf (errBuf, errCap,
				"CreateProcess failed (%lu); is Python on PATH?",
				(unsigned long)GetLastError ());
		CloseHandle (stdoutRd);
		CloseHandle (stdoutWr);
		CloseHandle (stdinRd);
		CloseHandle (stdinWr);
		return 0;
	}

	CloseHandle (pi.hThread);
	CloseHandle (stdinRd);
	CloseHandle (stdoutWr);

	procHandle = pi.hProcess;
	childStdinWr = stdinWr;
	childStdoutRd = stdoutRd;
	return 1;
}

void
AiProc_Kill (void)
{
	if (childStdinWr != NULL)
	{	/* Closing stdin ends the sidecar's read loop cleanly. */
		CloseHandle (childStdinWr);
		childStdinWr = NULL;
	}
	if (procHandle != NULL)
	{
		if (WaitForSingleObject (procHandle, 2000) != WAIT_OBJECT_0)
			TerminateProcess (procHandle, 0);
		CloseHandle (procHandle);
		procHandle = NULL;
	}
	if (childStdoutRd != NULL)
	{
		CloseHandle (childStdoutRd);
		childStdoutRd = NULL;
	}
}

int
AiProc_Write (const char *data, size_t len)
{
	if (childStdinWr == NULL)
		return 0;

	while (len > 0)
	{
		DWORD written = 0;

		if (!WriteFile (childStdinWr, data, (DWORD)len, &written, NULL)
				|| written == 0)
			return 0;
		data += written;
		len -= written;
	}
	return 1;
}

int
AiProc_ReadLine (char *buf, size_t cap, int timeoutMs,
		AiProc_WaitFn onWait)
{
	size_t n = 0;
	int waited = 0;

	if (childStdoutRd == NULL)
		return 0;

	while (n + 1 < cap)
	{
		DWORD avail = 0;
		DWORD got = 0;
		char c;

		if (!PeekNamedPipe (childStdoutRd, NULL, 0, NULL, &avail, NULL))
			return 0;

		if (avail == 0)
		{	/* Poll rather than block, so a wedged sidecar cannot hang the
			 * game indefinitely. */
			if (waited >= timeoutMs)
				return 0;
			if (onWait != NULL)
				onWait ();
			Sleep (10);
			waited += 10;
			continue;
		}

		if (!ReadFile (childStdoutRd, &c, 1, &got, NULL) || got == 0)
			return 0;

		if (c == '\n')
			break;
		if (c != '\r')
			buf[n++] = c;
	}

	buf[n] = '\0';
	return 1;
}

#else /* POSIX */

#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <sys/select.h>
#include <sys/types.h>
#include <sys/wait.h>

static pid_t childPid = 0;
static int toChild = -1;
static int fromChild = -1;

int
AiProc_Spawn (const char *workingDir, char *errBuf, size_t errCap)
{
	int inPipe[2];
	int outPipe[2];
	pid_t pid;

	if (pipe (inPipe) != 0)
	{
		snprintf (errBuf, errCap, "pipe failed");
		return 0;
	}
	if (pipe (outPipe) != 0)
	{
		snprintf (errBuf, errCap, "pipe failed");
		close (inPipe[0]);
		close (inPipe[1]);
		return 0;
	}

	pid = fork ();
	if (pid < 0)
	{
		snprintf (errBuf, errCap, "fork failed");
		close (inPipe[0]);
		close (inPipe[1]);
		close (outPipe[0]);
		close (outPipe[1]);
		return 0;
	}

	if (pid == 0)
	{	/* child */
		dup2 (inPipe[0], STDIN_FILENO);
		dup2 (outPipe[1], STDOUT_FILENO);
		close (inPipe[0]);
		close (inPipe[1]);
		close (outPipe[0]);
		close (outPipe[1]);
		if (workingDir != NULL && chdir (workingDir) != 0)
			_exit (127);
		execlp ("python3", "python3", "-m", "uqm_ai", "--provider", "claude",
				(char *)NULL);
		execlp ("python", "python", "-m", "uqm_ai", "--provider", "claude",
				(char *)NULL);
		_exit (127);
	}

	close (inPipe[0]);
	close (outPipe[1]);

	childPid = pid;
	toChild = inPipe[1];
	fromChild = outPipe[0];
	return 1;
}

void
AiProc_Kill (void)
{
	if (toChild >= 0)
	{
		close (toChild);
		toChild = -1;
	}
	if (childPid > 0)
	{
		int status;

		kill (childPid, SIGTERM);
		waitpid (childPid, &status, 0);
		childPid = 0;
	}
	if (fromChild >= 0)
	{
		close (fromChild);
		fromChild = -1;
	}
}

int
AiProc_Write (const char *data, size_t len)
{
	if (toChild < 0)
		return 0;

	while (len > 0)
	{
		ssize_t written = write (toChild, data, len);

		if (written <= 0)
		{
			if (written < 0 && errno == EINTR)
				continue;
			return 0;
		}
		data += written;
		len -= (size_t)written;
	}
	return 1;
}

int
AiProc_ReadLine (char *buf, size_t cap, int timeoutMs,
		AiProc_WaitFn onWait)
{
	size_t n = 0;
	int waited = 0;
	int ready;

	if (fromChild < 0)
		return 0;

	while (n + 1 < cap)
	{
		fd_set rd;
		struct timeval tv;
		char c;
		ssize_t got;

		FD_ZERO (&rd);
		FD_SET (fromChild, &rd);
		/* Short slices so onWait runs regularly rather than once at the end. */
		tv.tv_sec = 0;
		tv.tv_usec = 10000;

		ready = select (fromChild + 1, &rd, NULL, NULL, &tv);
		if (ready < 0)
			return 0;
		if (ready == 0)
		{
			if (waited >= timeoutMs)
				return 0;
			if (onWait != NULL)
				onWait ();
			waited += 10;
			continue;
		}

		got = read (fromChild, &c, 1);
		if (got <= 0)
			return 0;

		if (c == '\n')
			break;
		if (c != '\r')
			buf[n++] = c;
	}

	buf[n] = '\0';
	return 1;
}

#endif
