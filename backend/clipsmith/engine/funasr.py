"""FunASR engine (the ASR half of the FunClip pipeline, without Gradio/moviepy).

FunClip's `VideoClipper.recog` is a thin wrapper around `AutoModel.generate`;
calling the model directly keeps this project free of moviepy 1.x pinning and
lets us keep one decoded video out of memory. Sentence timestamps come back in
milliseconds, so they are normalised to seconds here.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..media import extract_wav
from ..models import Sentence, Transcript

_MODEL_LOCK = threading.Lock()


class FunASREngine:
    name = "funasr"

    def __init__(
        self,
        model: str = "paraformer-zh",
        language: str = "zh",
        device: str = "cpu",
        hotwords: str = "",
        speaker_diarization: bool = False,
    ) -> None:
        self.model_name = model
        self.language = language
        self.device = device
        self.hotwords = hotwords
        self.speaker_diarization = speaker_diarization
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with _MODEL_LOCK:
            if self._model is not None:
                return self._model
            try:
                from funasr import AutoModel
            except ImportError as exc:  # pragma: no cover - depends on optional deps
                raise RuntimeError(
                    "funasr is not installed. Run: pip install -e '.[asr]' "
                    "or set CLIPSMITH_ENGINE=mock"
                ) from exc
            kwargs: dict[str, Any] = {
                "model": self.model_name,
                "vad_model": "fsmn-vad",
                "vad_kwargs": {"max_single_segment_time": 30000},
                "punc_model": "ct-punc",
                "device": self.device,
                "disable_update": True,
            }
            if self.speaker_diarization:
                kwargs["spk_model"] = "cam++"
            self._model = AutoModel(**kwargs)
            return self._model

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        model = self._load()
        wav = extract_wav(media_path, work_dir / "audio.wav")
        result = model.generate(
            input=str(wav),
            cache={},
            is_final=True,
            sentence_timestamp=True,
            return_raw_text=True,
            hotword=self.hotwords or None,
        )
        if not result:
            raise RuntimeError("ASR returned no result")
        item = result[0]
        sentences = _parse_sentences(item)
        if not sentences:
            raise RuntimeError("ASR returned no sentence timestamps")
        duration = max(s.end for s in sentences)
        return Transcript(
            language=self.language,
            duration=duration,
            sentences=sentences,
            engine=self.name,
            meta={"model": self.model_name, "raw_text": item.get("text", "")},
        )


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return str(value or "")


def _parse_sentences(item: dict[str, Any]) -> list[Sentence]:
    rows = item.get("sentence_info") or []
    sentences: list[Sentence] = []
    for index, row in enumerate(rows):
        stamps = row.get("timestamp") or row.get("timestamps") or []
        if not stamps:
            continue
        text = _as_text(row.get("text") or row.get("text_tn")).strip()
        if not text:
            continue
        speaker = row.get("spk")
        sentences.append(
            Sentence(
                id=index,
                start=round(float(stamps[0][0]) / 1000.0, 3),
                end=round(float(stamps[-1][1]) / 1000.0, 3),
                text=text,
                speaker=str(speaker) if speaker is not None else None,
            )
        )

    if sentences:
        return sentences

    # Some models return only a flat token-level timestamp list.
    stamps = item.get("timestamp") or item.get("timestamps") or []
    text = _as_text(item.get("text") or item.get("text_tn")).strip()
    if text and stamps:
        return [
            Sentence(
                id=0,
                start=round(float(stamps[0][0]) / 1000.0, 3),
                end=round(float(stamps[-1][1]) / 1000.0, 3),
                text=text,
            )
        ]
    return []