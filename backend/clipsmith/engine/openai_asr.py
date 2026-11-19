"""Cloud ASR over any OpenAI-compatible transcription endpoint.

Groq, OpenAI and self-hosted whisper servers all expose
`POST /v1/audio/transcriptions`. The engine sends compressed mono audio and
requires `verbose_json` with segment timestamps, because the cutter maps
sentences back to video time; a provider that returns plain text cannot be used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..media import extract_audio
from ..models import Sentence, Transcript


def _field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def segments_to_sentences(segments: list[Any] | None) -> list[Sentence]:
    """Convert verbose_json segments (objects or dicts) into sentences."""
    sentences: list[Sentence] = []
    for segment in segments or []:
        try:
            start = float(_field(segment, "start"))
            end = float(_field(segment, "end"))
        except (TypeError, ValueError):
            continue
        text = str(_field(segment, "text") or "").strip()
        if not text or end <= start:
            continue
        sentences.append(
            Sentence(
                id=len(sentences),
                start=round(max(0.0, start), 3),
                end=round(end, 3),
                text=text,
            )
        )
    return sentences


class OpenAIASREngine:
    name = "openai-asr"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        language: str = "zh",
        timeout: float = 180.0,
    ) -> None:
        if not base_url or not model:
            raise ValueError("cloud ASR requires a base URL and a model name")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language = language
        self.timeout = timeout

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - core dependency
            raise RuntimeError("openai is not installed; pip install -e .") from exc

        audio = extract_audio(media_path, work_dir / "audio.mp3")
        client = OpenAI(
            api_key=self.api_key or "local",
            base_url=self.base_url,
            timeout=self.timeout,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
        }
        if self.language:
            payload["language"] = self.language

        with audio.open("rb") as handle:
            response = client.audio.transcriptions.create(file=handle, **payload)

        segments = _field(response, "segments") or []
        sentences = segments_to_sentences(segments)
        if not sentences:
            raise RuntimeError(
                "云端 ASR 没有返回分段时间戳（只返回文本）；"
                "这个服务不能用于按句剪辑，换一个支持 verbose_json segments 的接口"
            )

        return Transcript(
            language=str(_field(response, "language") or self.language or "unknown"),
            duration=max(sentence.end for sentence in sentences),
            sentences=sentences,
            engine=self.name,
            meta={
                "model": self.model,
                "base_url": self.base_url,
                "segments": len(sentences),
            },
        )