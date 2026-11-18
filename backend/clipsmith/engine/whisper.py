"""faster-whisper engine: real ASR with no torch dependency at runtime.

This is the lightest path to a truthful transcript: CTranslate2 int8 on CPU
handles a 30 s clip in seconds, and the model is pulled from the Hugging Face
cache on first use. Unlike the mock engine, the text and timestamps actually
match the audio, which matters as soon as the planner selects content.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Any

from ..media import extract_wav
from ..models import Sentence, Transcript

_MODEL_LOCK = threading.Lock()


def _read_pcm(path: Path) -> Any:
    """Read the 16 kHz mono WAV as float32 samples.

    faster-whisper's file path goes through PyAV, whose `av.open` signature
    changed between 18 and 19 and broke decoding. Handing the model a numpy
    array skips that layer entirely: ffmpeg already produced exactly the PCM
    the model wants.
    """
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise RuntimeError("expected mono 16-bit PCM from extract_wav")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


class WhisperEngine:
    name = "whisper"

    def __init__(
        self,
        model: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "zh",
        beam_size: int = 5,
    ) -> None:
        self.model_size = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with _MODEL_LOCK:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "faster-whisper is not installed. Run: pip install faster-whisper "
                    "or set CLIPSMITH_ENGINE=mock"
                ) from exc
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            return self._model

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        model = self._load()
        wav = extract_wav(media_path, work_dir / "audio.wav")
        samples = _read_pcm(wav)
        segments, info = model.transcribe(
            samples,
            language=self.language or None,
            beam_size=self.beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        sentences: list[Sentence] = []
        for index, segment in enumerate(segments):
            text = (segment.text or "").strip()
            if not text:
                continue
            start = max(0.0, float(segment.start))
            end = max(start + 0.2, float(segment.end))
            sentences.append(
                Sentence(
                    id=len(sentences),
                    start=round(start, 3),
                    end=round(end, 3),
                    text=text,
                )
            )
        if not sentences:
            raise RuntimeError("ASR returned no speech")
        return Transcript(
            language=(info.language or self.language or "unknown"),
            duration=max(s.end for s in sentences),
            sentences=sentences,
            engine=self.name,
            meta={
                "model": self.model_size,
                "language_probability": round(float(getattr(info, "language_probability", 0.0)), 3),
                "duration": round(float(getattr(info, "duration", 0.0)), 3),
            },
        )