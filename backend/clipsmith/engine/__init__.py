"""Engine registry: pick an implementation from configuration."""

from __future__ import annotations

from ..config import Settings
from .base import Engine
from .mock import MockEngine


def build_engine(settings: Settings) -> Engine:
    name = settings.engine.lower()
    if name == "mock":
        return MockEngine()
    if name == "funasr":
        from .funasr import FunASREngine

        return FunASREngine(
            model=settings.asr_model,
            language=settings.asr_language,
            device=settings.asr_device,
            hotwords=settings.asr_hotwords,
            speaker_diarization=settings.asr_speaker,
        )
    if name == "funclip":
        from .funclip import FunClipEngine

        return FunClipEngine(
            checkout=settings.funclip_path,
            language=settings.asr_language,
            hotwords=settings.asr_hotwords,
            speaker_diarization=settings.asr_speaker,
        )
    if name == "whisper":
        from .whisper import WhisperEngine

        return WhisperEngine(
            model=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute,
            language=settings.asr_language,
        )
    raise ValueError(
        f"unknown engine: {settings.engine!r} (use mock, whisper, funasr or funclip)"
    )