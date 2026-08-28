"""Generated speech: where it is written, and what it is named.

The sidecar owns a scratch directory for its lifetime and reports the native
path at handshake. The game mounts it into its virtual filesystem, so a clip
written here is addressable by the track player as an ordinary content file
without any change to the decoder or the track player itself.

Names are generated here and never taken from a model. Only the bare filename
crosses the wire, and the game refuses anything carrying a path separator, so
there are two independent reasons a model cannot name a file outside this
directory.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class AudioError(Exception):
    """Raised when the game's own audio cannot be read."""


def decode_to_wav(
    source: Path,
    target: Path,
    sample_rate: int,
    seconds: float | None = None,
) -> Path:
    """Decode one of the game's Ogg clips to mono WAV.

    ffmpeg rather than a Python audio stack: it is already needed to work
    with this content at all, and the alternative is several more large
    dependencies to read a handful of short files.

    seconds trims the result, which matters for a voice reference - cloners
    want a few clean seconds, and Fwiffo's lines run to half a minute.
    """
    command = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(source)]
    if seconds is not None:
        command += ["-t", f"{seconds:g}"]
    command += ["-ar", str(sample_rate), "-ac", "1", str(target)]

    try:
        subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise AudioError(
            "ffmpeg is not on PATH; it is needed to read the game's Ogg clips"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AudioError(
            f"ffmpeg could not decode {source}: "
            f"{exc.stderr.decode('utf-8', 'replace').strip()}"
        ) from exc
    return target


class VoiceDirectory:
    """A scratch directory for generated speech, removed on exit.

    RAII: the directory exists for as long as the object does. close() is
    idempotent so a caller may release it early without having to know
    whether anything else already has.
    """

    def __init__(self) -> None:
        self._path: Path | None = Path(
            tempfile.mkdtemp(prefix="uqm-ai-voice-")
        )
        self._sequence = 0

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("voice directory has been closed")
        return self._path

    @property
    def native_path(self) -> str:
        """The path as the game needs it, for uio_mountDir."""
        return str(self.path)

    def next_file(self) -> tuple[Path, str]:
        """Reserve a new clip path. Returns (full path, bare filename).

        Monotonic rather than content-derived: the same line spoken twice
        should not collide with a file the game may still be playing.
        """
        self._sequence += 1
        name = f"line-{self._sequence:05d}.wav"
        return self.path / name, name

    def prune(self, keep: int = 8) -> None:
        """Delete all but the most recent clips.

        A long conversation would otherwise accumulate one file per line for
        the whole session. Recent ones are kept because the player can replay
        the last exchange.
        """
        if self._path is None:
            return
        clips = sorted(self._path.glob("line-*.wav"))
        for stale in clips[:-keep] if keep else clips:
            try:
                stale.unlink()
            except OSError:
                # A clip the game still holds open is not worth failing over;
                # it will be pruned on a later turn.
                pass

    def close(self) -> None:
        if self._path is None:
            return
        shutil.rmtree(self._path, ignore_errors=True)
        self._path = None

    def __enter__(self) -> VoiceDirectory:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
