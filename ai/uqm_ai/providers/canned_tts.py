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
from pathlib import Path

from ..voice import AudioError, decode_to_wav
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

        # Decoded once, so every later line is a file copy.
        if self._wav is None:
            try:
                self._wav = decode_to_wav(
                    self._source,
                    Path(out_path).parent / "canned-source.wav",
                    sample_rate=22050,
                )
            except AudioError as exc:
                raise ProviderError(str(exc)) from exc

        shutil.copyfile(self._wav, out_path)
        return out_path
