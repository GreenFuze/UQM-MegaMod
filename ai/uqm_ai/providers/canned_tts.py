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
from typing import Mapping

from ..voice import AudioError, decode_to_wav
from .base import ProviderError, TTSProvider


class CannedTTS(TTSProvider):
    """Returns one of the speaking character's own clips for every line.

    The clip is chosen by character, so at least the wrong words are in the
    right voice - which is what makes it useful for checking that the game
    mounted the directory and loaded the file.
    """

    def __init__(self, sources: Mapping[str, Path]) -> None:
        missing = sorted(k for k, p in sources.items() if not Path(p).is_file())
        if missing:
            raise ProviderError(f"canned voice clips not found for: {missing}")
        if not sources:
            raise ProviderError("canned voice needs at least one clip")
        self._sources = dict(sources)
        self._wav: dict[str, Path] = {}

    @property
    def name(self) -> str:
        return "canned"

    def synthesise(self, text: str, character: str, out_path: str) -> str:
        del text  # deliberately ignored; that is what "canned" means

        source = self._sources.get(character)
        if source is None:
            raise ProviderError(f"no canned clip for {character!r}")

        # Decoded once per character, so every later line is a file copy.
        wav = self._wav.get(character)
        if wav is None:
            safe = character.replace(".", "-")
            try:
                wav = decode_to_wav(
                    source,
                    Path(out_path).parent / f"canned-{safe}.wav",
                    sample_rate=22050,
                )
            except AudioError as exc:
                raise ProviderError(str(exc)) from exc
            self._wav[character] = wav

        shutil.copyfile(wav, out_path)
        return out_path
