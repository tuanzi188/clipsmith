from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipsmith.draft_export import export_draft
from clipsmith.media import probe
from clipsmith.models import Clip, Sentence

pytest.importorskip("pyJianYingDraft")
pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


def _make_video(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )
    return path


def test_export_draft_with_video_and_subtitles(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "v.mp4")
    info = probe(source)
    sentences = [
        Sentence(id=0, start=0.0, end=2.0, text="第一句"),
        Sentence(id=1, start=2.0, end=4.0, text="第二句"),
    ]
    clip = Clip(
        id="c1", start=0.0, end=4.0, sentence_ids=[0, 1],
        hook="测试", score=0.9, reason="test",
    )
    out = export_draft(
        source, sentences, [clip], tmp_path / "drafts",
        name="demo", width=1080, height=1920,
    )
    assert (out / "draft_content.json").exists()
    assert (out / "draft_meta_info.json").exists()
    data = json.loads((out / "draft_content.json").read_text(encoding="utf-8"))
    types = [track["type"] for track in data["tracks"]]
    assert "video" in types
    assert "text" in types
    assert info.duration > 0
