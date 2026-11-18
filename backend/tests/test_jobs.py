from pathlib import Path

from clipsmith.jobs import JobStore
from clipsmith.models import Clip, Job


def _clip(clip_id: str) -> Clip:
    return Clip(
        id=clip_id,
        start=0.0,
        end=1.0,
        sentence_ids=[0],
        hook="h",
        score=1.0,
        reason="r",
    )


def test_cleanup_removes_orphan_renders(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    job: Job = store.create("a.mp4")
    job.clips = [_clip("keeper01")]
    directory = store.clips_dir(job.id)
    (directory / "keeper01.mp4").write_bytes(b"keep")
    (directory / "orphan01.mp4").write_bytes(b"drop")

    store.cleanup_clips(job)

    assert (directory / "keeper01.mp4").exists()
    assert not (directory / "orphan01.mp4").exists()


def test_cleanup_is_a_noop_without_clips_dir(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "data")
    job = store.create("a.mp4")
    store.cleanup_clips(job)  # must not raise