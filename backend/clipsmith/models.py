"""Plain dataclasses for the job/transcript/clip model, plus JSON (de)serialization.

Kept dependency-free on purpose: the store is plain files, and a job must stay
readable even if the process crashes mid-run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

JobStatus = Literal[
    "uploaded",
    "transcribing",
    "transcribed",
    "planning",
    "planned",
    "rendering",
    "ready",
    "error",
]

ClipStatus = Literal["planned", "rendering", "ready", "error"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Sentence:
    id: int
    start: float  # seconds
    end: float
    text: str
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Transcript:
    language: str
    duration: float
    sentences: list[Sentence]
    engine: str = "unknown"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Clip:
    id: str
    start: float
    end: float
    sentence_ids: list[int]
    hook: str
    score: float
    reason: str
    title: str = ""
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    status: ClipStatus = "planned"
    file: str | None = None  # relative to the job directory
    error: str | None = None
    # Sub-ranges after dead-air removal. Empty means "use start/end as-is".
    ranges: list[list[float]] = field(default_factory=list)
    removed_pauses: int = 0
    removed_seconds: float = 0.0

    @property
    def duration(self) -> float:
        if self.ranges:
            return sum(max(0.0, end - start) for start, end in self.ranges)
        return max(0.0, self.end - self.start)


@dataclass
class Job:
    id: str
    filename: str
    created_at: str = field(default_factory=now_iso)
    media_file: str = "source.mp4"
    duration: float | None = None
    status: JobStatus = "uploaded"
    progress: int = 0
    message: str = ""
    error: str | None = None
    has_audio: bool = True
    transcript: Transcript | None = None
    clips: list[Clip] = field(default_factory=list)
    export_aspect: str = "source"
    aspect_fit: str = "crop"
    export_subtitles: bool = False
    render_quality: str = "standard"
    trim_silence: bool = True
    chat_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Job":
        transcript = raw.get("transcript")
        if transcript:
            transcript = Transcript(
                language=transcript.get("language", "zh"),
                duration=float(transcript.get("duration", 0)),
                engine=transcript.get("engine", "unknown"),
                meta=transcript.get("meta") or {},
                sentences=[Sentence(**s) for s in transcript.get("sentences", [])],
            )
        clips = [Clip(**c) for c in raw.get("clips", [])]
        job = cls(
            id=raw["id"],
            filename=raw["filename"],
            created_at=raw.get("created_at", now_iso()),
            media_file=raw.get("media_file", "source.mp4"),
            duration=raw.get("duration"),
            status=raw.get("status", "uploaded"),
            progress=int(raw.get("progress", 0)),
            message=raw.get("message", ""),
            error=raw.get("error"),
            has_audio=bool(raw.get("has_audio", True)),
            transcript=transcript,
            clips=clips,
            export_aspect=raw.get("export_aspect", "source"),
            aspect_fit=raw.get("aspect_fit", "crop"),
            export_subtitles=bool(raw.get("export_subtitles", False)),
            render_quality=raw.get("render_quality", "standard"),
            trim_silence=bool(raw.get("trim_silence", True)),
            chat_pending=bool(raw.get("chat_pending", False)),
        )
        return job