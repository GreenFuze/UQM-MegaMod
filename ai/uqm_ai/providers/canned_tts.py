"""A TTS provider that synthesises nothing.

It copies one of the character's own canonical clips to the output path. The
words will not match, which is the point: it proves the audio path end to end
- sidecar writes, game mounts, track player loads, subtitles time against a
real clip of the right voice - without a model, a GPU or a download.

That is worth having permanently. It is the only way to tell a broken audio
pipeline apart from a broken synthesiser, and the two failures look identical
from inside the game.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import ProviderError, TTSProvider


class CannedTTS(TTSProvider):
    """Returns a fixed clip, transcoded to WAV, for every line."""

    def __init__(self, source_clip: Path) -> None:
        if not source_clip.is_file():
            raise ProviderError(f"canned voice clip not found: {source_clip}")
        self._source = source_clip
        self._wav: Path | None = None

    @property
    def name(self) -> str:
        return "canned"

    def synthesise(self, text: str, character: str, out_path: str) -> str:
        del text, character  # deliberately ignored; that is what "canned" means

        if self._wav is None:
            self._wav = self._to_wav(self._source, Path(out_path).parent)

        shutil.copyfile(self._wav, out_path)
        return out_path

    @staticmethod
    def _to_wav(source: Path, work_dir: Path) -> Path:
        """Decode the Ogg once, so every later line is a file copy.

        ffmpeg rather than a Python decoder: it is already a dependency of
        working with this content, and pulling in an audio stack for the
        stub would defeat the purpose of a provider that needs nothing.
        """
        target = work_dir / "canned-source.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y",
                    "-i", str(source),
                    "-ar", "22050", "-ac", "1",
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "ffmpeg is not on PATH; the canned voice needs it to decode "
                "the game's Ogg clips"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise ProviderError(
                f"ffmpeg could not decode {source}: "
                f"{exc.stderr.decode('utf-8', 'replace').strip()}"
            ) from exc
        return target
