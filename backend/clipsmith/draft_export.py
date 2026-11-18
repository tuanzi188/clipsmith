"""JianYing / CapCut draft export.

The draft is the deliverable that can still be edited: video track built from
the kept ranges, plus a subtitle track carrying the remapped timeline SRT.

pyJianYingDraft is an optional dependency (`pip install -e '.[draft]'`); the
draft folder is self-describing but references the source media by absolute
path, so keep the job directory around.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .media import ProbeInfo
from .models import Clip, Sentence
from .subtitles import timeline_srt


def canvas_for(aspect: str, info: ProbeInfo) -> tuple[int, int]:
    if aspect == "9:16":
        return 1080, 1920
    if aspect == "1:1":
        return 1080, 1080
    width = info.width or 1920
    height = info.height or 1080
    return width - width % 2, height - height % 2


def export_draft(
    source: Path,
    sentences: list[Sentence],
    clips: list[Clip],
    out_root: Path,
    *,
    name: str,
    width: int,
    height: int,
    fps: int = 30,
) -> Path:
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "pyJianYingDraft is not installed. Run: pip install -e '.[draft]'"
        ) from exc

    out_root.mkdir(parents=True, exist_ok=True)
    folder = draft.DraftFolder(str(out_root))
    script = folder.create_draft(name, width, height, fps=fps, allow_replace=True)
    script.append_track(draft.TrackSpec(draft.TrackType.video, "main"))

    material = draft.VideoMaterial(str(source))
    # The container duration can be a few ms longer than the video stream, so
    # clamp every source range to what JianYing's material reader reports.
    material_seconds = material.duration / 1_000_000
    cursor = 0.0
    for clip in clips:
        spans = clip.ranges or [[clip.start, clip.end]]
        for begin, end in spans:
            begin = max(0.0, min(begin, material_seconds))
            end = max(begin, min(end, material_seconds - 0.01))
            duration = end - begin
            if duration <= 0.05:
                continue
            segment = draft.VideoSegment(
                material,
                draft.trange_seconds(cursor, duration=duration),
                source_timerange=draft.trange_seconds(begin, duration=duration),
            )
            script.add_segment(segment, "main")
            cursor += duration

    srt = timeline_srt(sentences, clips)
    if srt.strip():
        srt_path = out_root / f"{name}.srt"
        srt_path.write_text(srt, encoding="utf-8")
        try:
            script.import_srt(str(srt_path), track_name="字幕")
        finally:
            srt_path.unlink(missing_ok=True)

    script.save()
    return out_root / name


def zip_draft(draft_dir: Path) -> Path:
    archive_base = draft_dir.parent / f"{draft_dir.name}"
    path = shutil.make_archive(
        str(archive_base), "zip", root_dir=draft_dir.parent, base_dir=draft_dir.name
    )
    return Path(path)
