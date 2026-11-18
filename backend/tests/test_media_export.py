from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from clipsmith.media import (
    burn_subtitles,
    normalize_loudness,
    probe,
    render_clip,
    render_ranges,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


def _make_video(path: Path, seconds: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=15:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )
    return path


def test_burn_subtitles_keeps_duration(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4")
    before = probe(video)
    srt = "1\n00:00:00,000 --> 00:00:01,500\n测试字幕\n"
    burn_subtitles(video, srt, style="clean")
    after = probe(video)
    assert after.duration == pytest.approx(before.duration, abs=0.3)
    assert video.exists()
    assert not (tmp_path / "v.burn.srt").exists()


def test_burn_subtitles_ignores_empty_text(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4", 1.0)
    size = video.stat().st_size
    burn_subtitles(video, "   \n")
    assert video.stat().st_size == size


def _integrated_loudness(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "loudnorm=print_format=json", "-f", "null", "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )
    text = proc.stderr.decode("utf-8", "replace")
    data = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S).group(0))
    return float(data["input_i"])


def test_blur_fit_keeps_vertical_canvas(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4", 1.0)
    output = tmp_path / "blur.mp4"
    render_clip(
        video, output, 0.0, 1.0, aspect="9:16", fit="blur", quality="draft"
    )
    info = probe(output)
    assert (info.width, info.height) == (1080, 1920)


def test_render_clip_burns_ass_in_the_same_pass(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4", 1.0)
    output = tmp_path / "sub.mp4"
    render_clip(
        video,
        output,
        0.0,
        1.0,
        aspect="9:16",
        quality="draft",
        subtitles=[(0.1, 0.9, "测试字幕")],
    )
    assert output.exists()
    assert probe(output).duration == pytest.approx(1.0, abs=0.2)
    assert not (tmp_path / "sub.ass").exists()


def test_render_ranges_splices_and_renormalises(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4", 2.6)
    output = tmp_path / "cut.mp4"
    render_ranges(
        video,
        output,
        [(0.0, 1.0), (1.5, 2.4)],
        quality="draft",
        subtitles=[(0.2, 0.8, "一"), (1.6, 2.2, "二")],
    )
    assert probe(output).duration == pytest.approx(1.9, abs=0.25)


def test_quality_presets_trade_size_for_bitrate(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "v.mp4", 1.5)
    draft = tmp_path / "draft.mp4"
    final = tmp_path / "final.mp4"
    render_clip(video, draft, 0.0, 1.5, quality="draft")
    render_clip(video, final, 0.0, 1.5, quality="final")
    assert final.stat().st_size > draft.stat().st_size


def test_normalize_loudness_hits_platform_target(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "loud.mp4", 2.0)
    before = _integrated_loudness(video)
    assert before < -18  # lavfi's default sine is quiet
    normalize_loudness(video)
    after = _integrated_loudness(video)
    assert after == pytest.approx(-14.0, abs=1.5)
