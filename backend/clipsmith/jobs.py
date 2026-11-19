"""Job persistence: one directory per job, one JSON file per job.

A job directory looks like:

    data/jobs/<job_id>/
        job.json
        source.mp4
        audio.wav
        clips/<clip_id>.mp4

Everything is plain files so a crashed run can be inspected and resumed by hand.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path

from .models import Job

_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- paths -------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        if not _JOB_ID_RE.match(job_id):
            raise ValueError(f"invalid job id: {job_id!r}")
        return self.root / "jobs" / job_id

    def media_path(self, job_id: str, media_file: str = "source.mp4") -> Path:
        name = Path(media_file).name  # never allow traversal out of the job dir
        return self.job_dir(job_id) / name

    def audio_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "audio.wav"

    def chat_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "chat.json"

    def clips_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "clips"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def clip_path(self, job_id: str, clip_id: str) -> Path:
        if not _JOB_ID_RE.match(clip_id):
            raise ValueError(f"invalid clip id: {clip_id!r}")
        return self.clips_dir(job_id) / f"{clip_id}.mp4"

    def base_clip_path(self, job_id: str, clip_id: str) -> Path:
        """Narration-free render kept alongside a mixed clip.

        Voiceover is layered on top of this base instead of the already-mixed
        file, so re-recording narration replaces the old one instead of
        stacking a second track under the first.
        """
        if not _JOB_ID_RE.match(clip_id):
            raise ValueError(f"invalid clip id: {clip_id!r}")
        return self.clips_dir(job_id) / f"{clip_id}.base.mp4"

    # -- lifecycle ---------------------------------------------------------
    def create(self, filename: str) -> Job:
        job_id = f"{uuid.uuid4().hex[:8]}"
        self.job_dir(job_id).mkdir(parents=True, exist_ok=False)
        job = Job(id=job_id, filename=filename)
        self.save(job)
        return job

    def load(self, job_id: str) -> Job:
        path = self.job_dir(job_id) / "job.json"
        # Reads must take the same lock as writes: on Windows an open reader
        # makes os.replace fail with PermissionError.
        with self._lock_for(job_id):
            if not path.exists():
                raise FileNotFoundError(job_id)
            return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, job: Job) -> None:
        directory = self.job_dir(job.id)
        directory.mkdir(parents=True, exist_ok=True)
        lock = self._lock_for(job.id)
        with lock:
            target = directory / "job.json"
            tmp = directory / f"job.json.{uuid.uuid4().hex}.tmp"
            tmp.write_text(job.to_json(), encoding="utf-8")
            os.replace(tmp, target)

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def cleanup_clips(self, job: Job) -> None:
        """Remove rendered mp4s that no clip in the job references any more.

        Re-planning replaces clip ids, so without this every iteration leaves
        the old renders behind and the job directory only grows.
        """
        directory = self.job_dir(job.id) / "clips"
        if not directory.exists():
            return
        keep = {f"{clip.id}.mp4" for clip in job.clips}
        keep |= {f"{clip.id}.base.mp4" for clip in job.clips}
        for path in directory.glob("*.mp4"):
            if path.name not in keep:
                path.unlink(missing_ok=True)

    def list_recent(self, limit: int = 20) -> list[Job]:
        jobs_root = self.root / "jobs"
        candidates = sorted(
            (p for p in jobs_root.iterdir() if (p / "job.json").exists()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        result: list[Job] = []
        for path in candidates[:limit]:
            try:
                result.append(Job.from_dict(json.loads((path / "job.json").read_text("utf-8"))))
            except (json.JSONDecodeError, KeyError):
                continue
        return result

    def _lock_for(self, job_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(job_id, threading.Lock())