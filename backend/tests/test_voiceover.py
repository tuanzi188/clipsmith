from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clipsmith.jobs import JobStore
from clipsmith.models import Clip, Job
from clipsmith.voiceover import mix_voiceover, tempo_for

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


def _make_video(path: Path, seconds: float = 3.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=15:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
    )
    return path


def _make_narration(path: Path, seconds: float = 1.2) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=880:duration={seconds}",
            "-c:a", "libmp3lame", "-b:a", "48k", str(path),
        ],
        check=True,
    )
    return path


def test_tempo_only_speeds_up_and_caps() -> None:
    assert tempo_for(1.0, 5.0) == 1.0
    assert tempo_for(3.0, 2.0) == 1.25
    assert tempo_for(20.0, 2.0) == 1.25


def test_mix_keeps_video_length_and_audio(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    video = _make_video(tmp_path / "a" / "clip.mp4")
    # Narration lives outside the clip directory: the mix must use absolute
    # paths, not bare basenames resolved against cwd.
    narration = _make_narration(tmp_path / "b" / "vo.mp3")
    mixed = tmp_path / "a" / "mixed.mp4"
    result = mix_voiceover(video, narration, mixed)

    from clipsmith.media import probe

    info = probe(mixed)
    assert info.duration == pytest.approx(3.0, abs=0.25)
    assert info.has_audio
    assert result["narration_seconds"] == pytest.approx(1.2, abs=0.2)
    # The source is read-only input: it must survive untouched.
    assert probe(video).duration == pytest.approx(3.0, abs=0.25)
    assert (tmp_path / "a" / "mixed.vo.mp4").exists() is False
    assert (tmp_path / "a" / "clip.vo.mp4").exists() is False


def test_add_voiceover_rejects_unrendered_clip(tmp_path: Path) -> None:
    from clipsmith.voiceover import add_voiceover_to_clip

    store = JobStore(tmp_path / "data")
    job: Job = store.create("a.mp4")
    job.clips = [
        Clip(
            id="abc123",
            start=0.0,
            end=5.0,
            sentence_ids=[0],
            hook="h",
            score=1.0,
            reason="r",
            status="planned",
        )
    ]
    store.save(job)
    with pytest.raises(ValueError):
        add_voiceover_to_clip(store, job, "abc123", "测试旁白")


def test_rerecording_voiceover_reuses_clean_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second voiceover replaces the first instead of stacking on it."""
    from clipsmith import voiceover as vo

    store = JobStore(tmp_path / "data")
    job: Job = store.create("a.mp4")
    job.clips = [
        Clip(
            id="abc123",
            start=0.0,
            end=3.0,
            sentence_ids=[0],
            hook="h",
            score=1.0,
            reason="r",
            status="ready",
            file="clips/abc123.mp4",
        )
    ]
    store.save(job)
    _make_video(store.clip_path(job.id, "abc123"), 3.0)

    narration = _make_narration(tmp_path / "vo.mp3")

    def fake_synthesize(text: str, dst: Path, *, voice: str | None = None) -> Path:
        shutil.copy2(narration, dst)
        return Path(dst)

    monkeypatch.setattr(vo, "synthesize", fake_synthesize)

    base = store.base_clip_path(job.id, "abc123")
    assert base.exists() is False

    vo.add_voiceover_to_clip(store, job, "abc123", "第一条")
    assert base.exists()
    clean_base = base.read_bytes()

    vo.add_voiceover_to_clip(store, job, "abc123", "第二条")
    # The snapshot must not change: the second mix reads from it, not from the
    # already-narrated output.
    assert base.read_bytes() == clean_base
    assert store.load(job.id).clips[0].voiceover_text == "第二条"