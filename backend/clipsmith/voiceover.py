"""Edge-TTS voiceover: a narration track laid over the original audio.

The original voice is not replaced; it is ducked under the narration. This is
the cheap way to test the "AI commentary" shape without voice cloning, lip-sync
or any model download.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from .jobs import JobStore
from .media import _require, _run, normalize_loudness, probe
from .models import Clip, Job

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
VOICES = [
    "zh-CN-XiaoxiaoNeural",  # 女声，通用
    "zh-CN-YunxiNeural",  # 男声，年轻
    "zh-CN-YunjianNeural",  # 男声，浑厚
    "zh-CN-XiaoyiNeural",  # 女声，活泼
]


def synthesize(
    text: str,
    dst: str | Path,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
) -> Path:
    """Generate a narration mp3 with Edge-TTS (free, unofficial endpoint)."""
    text = text.strip()
    if not text:
        raise ValueError("voiceover text is empty")
    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts"
        ) from exc

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    async def _save() -> None:
        await edge_tts.Communicate(text, voice, rate=rate).save(str(dst))

    asyncio.run(_save())
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError("Edge-TTS returned no audio")
    return dst


def tempo_for(narration_seconds: float, available_seconds: float) -> float:
    """Speed factor needed to fit the narration; capped so it stays natural."""
    if available_seconds <= 0.1:
        return 1.0
    return min(1.25, max(1.0, narration_seconds / available_seconds))


def mix_voiceover(
    video: str | Path,
    narration: str | Path,
    target: str | Path,
    *,
    delay: float = 0.35,
    duck: float = 0.35,
) -> dict:
    """Mix narration over `video` and write the result to `target`.

    Reading and writing are separate paths so the narration-free source can be
    reused: mixing into the same file would stack a new track on top of the
    previous voiceover instead of replacing it. Video is stream-copied; only
    audio is re-encoded and loudness-normalised.
    """
    video = Path(video).resolve()
    narration = Path(narration).resolve()
    target = Path(target).resolve()
    info = probe(video)
    narration_seconds = probe(narration).duration
    available = max(0.1, info.duration - delay)
    tempo = tempo_for(narration_seconds, available)
    delay_ms = max(0, int(round(delay * 1000)))

    ffmpeg = _require("ffmpeg")
    temp = target.with_name(f"{target.stem}.vo.mp4")
    if info.has_audio:
        graph = (
            f"[0:a]volume={duck:.3f}[bg];"
            f"[1:a]atempo={tempo:.4f},adelay={delay_ms}:all=1[vo];"
            f"[bg][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
    else:
        graph = f"[1:a]atempo={tempo:.4f},adelay={delay_ms}:all=1[a]"

    try:
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video),
                "-i",
                str(narration),
                "-filter_complex",
                graph,
                "-map",
                "0:v:0?",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-t",
                f"{info.duration:.3f}",
                "-movflags",
                "+faststart",
                temp.name,
            ],
            cwd=target.parent,
        )
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)

    normalize_loudness(target)
    return {
        "narration_seconds": round(narration_seconds, 2),
        "tempo": round(tempo, 3),
        "trimmed": narration_seconds > available * tempo + 0.15,
    }


def add_voiceover_to_clip(
    store: JobStore,
    job: Job,
    clip_id: str,
    text: str,
    *,
    voice: str | None = None,
) -> dict:
    """Synthesize narration and mix it into an already rendered clip.

    The first call snapshots the clean render as `clips/<id>.base.mp4`; every
    call mixes from that base into `clips/<id>.mp4`. Re-recording a voiceover
    therefore replaces the previous one instead of stacking a second track.
    """
    clip: Clip | None = next((item for item in job.clips if item.id == clip_id), None)
    if clip is None:
        raise ValueError(f"找不到片段 {clip_id}")
    if clip.status != "ready" or not clip.file:
        raise ValueError("这个片段还没有导出，先出片再加旁白")

    source = store.clip_path(job.id, clip.id)
    if not source.exists():
        raise ValueError("片段文件不存在，请重新导出")
    base = store.base_clip_path(job.id, clip.id)
    if not base.exists():
        shutil.copy2(source, base)

    narration = store.job_dir(job.id) / f"voiceover-{clip.id}.mp3"
    try:
        synthesize(text, narration, voice=voice or DEFAULT_VOICE)
        result = mix_voiceover(base, narration, source)
    finally:
        narration.unlink(missing_ok=True)

    clip.voiceover_text = text.strip()[:200]
    store.save(job)
    return result