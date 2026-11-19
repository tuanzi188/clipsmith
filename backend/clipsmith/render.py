"""Shared render pipeline used by both the HTTP API and the agent."""

from __future__ import annotations

from pathlib import Path

from .jobs import JobStore
from .media import MediaError, render_ranges
from .models import Job


def render_selected(
    store: JobStore,
    job: Job,
    clip_ids: list[str] | None = None,
    *,
    aspect: str | None = None,
    subtitles: bool | None = None,
    subtitle_style: str = "clean",
    quality: str | None = None,
    fit: str | None = None,
) -> Job:
    source: Path = store.media_path(job.id, job.media_file)
    targets = [
        clip for clip in job.clips if clip_ids is None or clip.id in set(clip_ids)
    ]
    if not targets:
        return job

    use_aspect = aspect or job.export_aspect or "source"
    use_subtitles = job.export_subtitles if subtitles is None else subtitles
    use_quality = quality or job.render_quality or "standard"
    use_fit = fit or job.aspect_fit or "crop"
    entries = (
        [(s.start, s.end, s.text) for s in job.transcript.sentences]
        if use_subtitles and job.transcript
        else None
    )

    job.status = "rendering"
    job.progress = 0
    job.message = f"导出 0/{len(targets)}"
    job.error = None
    store.save(job)

    for index, clip in enumerate(targets, start=1):
        clip.status = "rendering"
        clip.error = None
        clip.file = None
        store.save(job)
        try:
            destination = store.clip_path(job.id, clip.id)
            ranges = clip.ranges or [[clip.start, clip.end]]
            render_ranges(
                source,
                destination,
                ranges,
                aspect=use_aspect,
                fit=use_fit,
                quality=use_quality,
                subtitles=entries,
                subtitle_style=subtitle_style,
            )
            clip.file = f"clips/{clip.id}.mp4"
            clip.status = "ready"
            # A fresh render has no narration: drop the snapshot the voiceover
            # mixer would otherwise reuse, and stop advertising old narration.
            store.base_clip_path(job.id, clip.id).unlink(missing_ok=True)
            clip.voiceover_text = ""
        except (MediaError, OSError) as exc:  # noqa: BLE001 - per-clip failure
            clip.status = "error"
            clip.error = str(exc)[:500]
        job.progress = int(100 * index / len(targets))
        job.message = f"导出 {index}/{len(targets)}"
        store.save(job)

    ready = sum(1 for clip in targets if clip.status == "ready")
    job.status = "ready" if ready else "error"
    job.progress = 100
    job.message = f"{ready}/{len(targets)} 个片段导出完成"
    store.save(job)
    return job