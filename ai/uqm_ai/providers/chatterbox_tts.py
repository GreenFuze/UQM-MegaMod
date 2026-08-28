"""Each character's own voice, cloned zero-shot from the recordings they ship.

References are built on the player's machine from the voice pack they already
have. Nothing cloned is committed, downloaded from us, or shipped: the model is
a general-purpose one, and the only thing that makes it sound like anyone is a
file already sitting in their content directory.

One model serves the whole cast - it costs 23 seconds to load and 3 GiB of
VRAM, so a process per character is not an option - and the reference clip is
what decides who is speaking.

Chatterbox is MIT-licensed and needs no training: a few seconds of clean
reference audio is enough, which is why it is the first thing tried rather than
fine-tuning, despite there being 336 seconds of aligned Spathi corpus available
if that turns out to be necessary.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping

from .. import gamelog
from ..voice import AudioError, decode_to_wav
from .base import ProviderError, TTSProvider

# Cloners want a few clean seconds. Fwiffo's lines run to half a minute, and
# handing over all of it is slower without being better.
REFERENCE_SECONDS = 15.0

# Chatterbox trains at 24 kHz; the reference is resampled to match so no
# conversion happens inside the model.
REFERENCE_RATE = 24000


class ChatterboxVoice(TTSProvider):
    """Zero-shot voice cloning on the GPU, warmed up in the background."""

    def __init__(
        self,
        reference_clips: Mapping[str, Path],
        device: str | None = None,
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
    ) -> None:
        if not reference_clips:
            raise ProviderError("voice cloning needs at least one reference clip")
        missing = sorted(k for k, p in reference_clips.items() if not Path(p).is_file())
        if missing:
            raise ProviderError(f"voice references not found for: {missing}")

        self._exaggeration = exaggeration
        self._cfg_weight = cfg_weight
        self._work = Path(tempfile.mkdtemp(prefix="uqm-ai-ref-"))
        self._clips = dict(reference_clips)

        # Decoded on first use per character rather than all at once: a player
        # speaks to two or three characters in a session, and ffmpeg on 27
        # files at startup is time nobody gets back.
        self._references: dict[str, Path] = {}
        self._warm_key = sorted(self._clips)[0]
        self._reference = self._reference_for(self._warm_key)

        self._model = None
        self._load_error: BaseException | None = None

        # Loading costs seconds and would otherwise be paid either at startup,
        # delaying the game, or on the first spoken line, stalling a
        # conversation that is already slow. Neither is necessary: the player
        # needs minutes to reach anyone worth talking to.
        self._ready = threading.Event()
        threading.Thread(target=self._load, name="tts-warmup", daemon=True).start()

    @property
    def name(self) -> str:
        return "chatterbox"

    def _reference_for(self, character: str) -> Path:
        """The decoded reference for one character, cut to a few clean seconds."""
        reference = self._references.get(character)
        if reference is not None:
            return reference

        clip = self._clips.get(character)
        if clip is None:
            raise ProviderError(f"no voice reference for {character!r}")

        safe = character.replace(".", "-")
        try:
            reference = decode_to_wav(
                clip,
                self._work / f"reference-{safe}.wav",
                sample_rate=REFERENCE_RATE,
                seconds=REFERENCE_SECONDS,
            )
        except AudioError as exc:
            raise ProviderError(str(exc)) from exc

        self._references[character] = reference
        return reference

    def _load(self) -> None:
        try:
            import torch
            from chatterbox.tts import ChatterboxTTS

            device = "cuda" if torch.cuda.is_available() else "cpu"

            started = time.perf_counter()
            model = ChatterboxTTS.from_pretrained(device)
            loaded = time.perf_counter()
            self._model = model

            # The first generate pays CUDA kernel autotuning: measured at
            # 29s against 9s for every line after it, on a longer line. Spent
            # here it is a warm-up nobody sees; spent on demand it is a first
            # reply that looks like the game has hung.
            #
            # A failure here is not a failure to load. The model is already
            # usable, and the only thing lost is the head start.
            try:
                model.generate(
                    "Hello.", audio_prompt_path=str(self._reference)
                )
            except Exception as exc:  # noqa: BLE001
                gamelog.emit(f"voice warm-up failed: {exc}")

            gamelog.emit(
                f"voice ready on {device}: loaded in "
                f"{loaded - started:.1f}s, warmed in "
                f"{time.perf_counter() - loaded:.1f}s"
            )
        except BaseException as exc:  # noqa: BLE001 - reported on first use
            self._load_error = exc
        finally:
            self._ready.set()

    def synthesise(self, text: str, character: str, out_path: str) -> str:
        # One model, many voices: the reference clip decides who is speaking,
        # and it is the character's own recording from the player's content.
        reference = self._reference_for(character)

        self._ready.wait()
        if self._model is None:
            raise ProviderError(f"voice model failed to load: {self._load_error}")

        import torchaudio

        started = time.perf_counter()
        wav = self._model.generate(
            text,
            audio_prompt_path=str(reference),
            exaggeration=self._exaggeration,
            cfg_weight=self._cfg_weight,
        )
        # 16-bit PCM, not the float that torchaudio writes by default. UQM's
        # decoder takes format 0x0001 and nothing else, so a float WAV is not
        # merely larger - it cannot be opened at all, and the failure looks
        # exactly like a hang: the line is synthesised, SpliceTrack refuses
        # it, no subtitle appears, and the screen sits on "(transmitting)".
        torchaudio.save(
            out_path,
            wav.cpu(),
            self._model.sr,
            encoding="PCM_S",
            bits_per_sample=16,
        )

        seconds = wav.shape[-1] / self._model.sr
        gamelog.emit(
            f"{character}: spoke {len(text)} chars as {seconds:.1f}s of audio "
            f"in {time.perf_counter() - started:.1f}s"
        )
        return out_path
