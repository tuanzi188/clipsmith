"""Adapter for a vendored FunClip checkout.

Use this if you already have FunClip installed and want its exact recognition
(+ speaker diarization) path. It reuses `VideoClipper.recog`; rendering is still
done by ffmpeg in this project, so FunClip's `video_clip` / moviepy state is not
used at all.

Note: FunClip uses top-level imports (`from utils.subtitle_utils import ...`),
so its checkout and its `funclip/` directory must both be on sys.path.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from ..media import extract_wav
from ..models import Sentence, Transcript

_LOCK = threading.Lock()
_MODEL_ALIASES = {
    "paraformer": "paraformer-zh",
    "sensevoice": "iic/SenseVoiceSmall",
}


class FunClipEngine:
    name = "funclip"

    def __init__(
        self,
        checkout: str | Path,
        language: str = "zh",
        model: str = "paraformer",
        hotwords: str = "",
        speaker_diarization: bool = False,
    ) -> None:
        self.checkout = Path(checkout)
        self.language = language
        self.model = _MODEL_ALIASES.get(model, model)
        self.hotwords = hotwords
        self.speaker_diarization = speaker_diarization
        self._clipper: Any | None = None

    def _load(self) -> Any:
        if self._clipper is not None:
            return self._clipper
        with _LOCK:
            if self._clipper is not None:
                return self._clipper
            package_dir = self.checkout / "funclip"
            if not package_dir.exists():
                raise RuntimeError(
                    f"FunClip checkout not found at {self.checkout}. "
                    "Clone it there or set CLIPSMITH_FUNCLIP_PATH."
                )
            for entry in (str(self.checkout.resolve()), str(package_dir.resolve())):
                if entry not in sys.path:
                    sys.path.insert(0, entry)
            try:
                from funasr import AutoModel
                from funclip.videoclipper import VideoClipper
            except ImportError as exc:  # pragma: no cover - optional deps
                raise RuntimeError(
                    "FunClip/FunASR not installed. Use CLIPSMITH_ENGINE=funasr or mock."
                ) from exc
            model = AutoModel(
                model=self.model,
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                disable_update=True,
            )
            clipper = VideoClipper(model)
            clipper.lang = self.language
            self._clipper = clipper
            return clipper

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        clipper = self._load()
        wav_path = extract_wav(media_path, work_dir / "audio.wav")
        import librosa

        data, sr = librosa.load(str(wav_path), sr=16000, mono=True)
        _, _, state = clipper.recog(
            (sr, data),
            sd_switch="Yes" if self.speaker_diarization else "no",
            state=None,
            hotwords=self.hotwords,
            output_dir=str(work_dir),
        )
        rows = state.get("sentences") or []
        sentences: list[Sentence] = []
        for index, row in enumerate(rows):
            stamps = row.get("timestamp") or []
            text = row.get("text") or ""
            if isinstance(text, list):
                text = "".join(str(t) for t in text)
            text = str(text).strip()
            if not stamps or not text:
                continue
            speaker = row.get("spk")
            speaker = speaker if speaker is not None else row.get("spk_name")
            sentences.append(
                Sentence(
                    id=index,
                    start=round(float(stamps[0][0]) / 1000.0, 3),
                    end=round(float(stamps[-1][1]) / 1000.0, 3),
                    text=text,
                    speaker=str(speaker) if speaker is not None else None,
                )
            )
        if not sentences:
            raise RuntimeError("FunClip returned no sentence timestamps")
        return Transcript(
            language=self.language,
            duration=max(s.end for s in sentences),
            sentences=sentences,
            engine=self.name,
            meta={"model": self.model, "srt": state.get("srt", "")},
        )