"""Diagnostics that reach the game's log.

The sidecar's stderr is inherited from the game, and in practice it does not
survive: nothing the sidecar printed - synthesis timings, provider warnings,
tracebacks - ever appeared in a game log, which meant the log showed the
game's view of the sidecar and never the sidecar's view of itself.

So diagnostics travel over the protocol instead, as `{"type":"log"}` lines the
game turns into ordinary log_add entries. That works however the game was
launched, puts sidecar and game events in one file in the right order, and is
the only form a player could ever be asked to send us.

Writes are serialised because the voice model warms up on its own thread and
would otherwise interleave a log line into the middle of a reply.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import IO

_lock = threading.Lock()
_wire: IO[str] | None = None

# Long enough for a traceback line, short enough that a burst cannot fill the
# pipe while the game is busy rendering and not reading.
MAX_MESSAGE = 500


def attach(wire: IO[str]) -> None:
    """Send diagnostics to the game from now on."""
    global _wire
    with _lock:
        _wire = wire


def writer_lock() -> threading.Lock:
    """Held while writing a message, so replies and log lines never interleave.

    The sidecar's own replies take it too: one wire, one writer at a time.
    """
    return _lock


def emit(message: str) -> None:
    """Report one line. Never raises: logging must not break a turn."""
    text = message.strip()[:MAX_MESSAGE]
    if not text:
        return

    with _lock:
        if _wire is None:
            print(f"[uqm-ai] {text}", file=sys.stderr, flush=True)
            return
        try:
            _wire.write(
                json.dumps(
                    {"type": "log", "message": text}, ensure_ascii=False
                )
                + "\n"
            )
            _wire.flush()
        except Exception:  # noqa: BLE001
            pass
