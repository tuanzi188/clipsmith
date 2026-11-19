"""ffmpeg/ffprobe helpers.

Rendering deliberately does NOT go through moviepy: cutting N ranges is a
pure ffmpeg problem, it is faster, and it avoids keeping a decoded video
object alive between HTTP requests (which is what the Gradio app did).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .subtitles import ass_document, span_entries

Aspect = str  # "source" | "9:16" | "1:1"
Fit = str  # "crop" | "blur"

# Delivery presets. "standard" is the old CRF 20 but at the default x264 preset
# instead of veryfast, which is the same quality for ~20% fewer bits.
_QUALITY = {
    "draft": (23, "veryfast"),
    "standard": (20, "medium"),
    "final": (17, "slow"),
}
_ASPECT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080)}
_SUBTITLE_STYLES = {
    "clean": (
        "FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H80000000,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginV=44"
    ),
    "bold": (
        "FontName=Microsoft YaHei,FontSize=28,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,Bold=1,Alignment=2,MarginV=70"
    ),
}
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_LOUDNESS_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


class MediaError(RuntimeError):
    pass


@dataclass
class ProbeInfo:
    duration: float
    has_audio: bool
    has_video: bool
    width: int | None = None
    height: int | None = None


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise MediaError(f"{tool} not found on PATH; install ffmpeg first")
    return path


def _run(cmd: list[str], timeout: float = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        cwd=str(cwd) if cwd else None,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace")[-1500:]
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")
    return proc


def probe(path: str | Path) -> ProbeInfo:
    ffprobe = _require("ffprobe")
    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if duration <= 0 and video and video.get("duration"):
        duration = float(video["duration"])
    return ProbeInfo(
        duration=duration,
        has_audio=audio is not None,
        has_video=video is not None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
    )


def extract_wav(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """Mono 16 kHz PCM, the format FunASR expects."""
    ffmpeg = _require("ffmpeg")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    return dst


def extract_audio(
    src: str | Path,
    dst: str | Path,
    *,
    sample_rate: int = 16000,
    bitrate: str = "48k",
) -> Path:
    """Mono 16 kHz compressed audio, sized for cloud ASR upload limits.

    48 kbps keeps a 10 minute clip near 3.6 MB, well under the usual 25 MB
    request cap. The container is inferred from the destination extension.
    """
    ffmpeg = _require("ffmpeg")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-b:a",
            bitrate,
            str(dst),
        ]
    )
    return dst


def detect_silences(
    src: str | Path,
    *,
    noise_db: int = -35,
    min_duration: float = 0.45,
) -> list[tuple[float, float]]:
    """Return dead-air ranges in seconds, via ffmpeg's silencedetect.

    This is the same primitive auto-editor uses for its default cut: no model,
    no transcript, works on any footage with audio.
    """
    ffmpeg = _require("ffmpeg")
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(src),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    text = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise MediaError(f"silencedetect failed: {text[-800:]}")

    ranges: list[tuple[float, float]] = []
    start: float | None = None
    for line in text.splitlines():
        match = _SILENCE_START.search(line)
        if match:
            start = max(0.0, float(match.group(1)))
            continue
        match = _SILENCE_END.search(line)
        if match and start is not None:
            end = float(match.group(1))
            if end > start:
                ranges.append((start, end))
            start = None

    # Merge ranges that only a few milliseconds apart (silencedetect can split).
    merged: list[tuple[float, float]] = []
    for begin, end in ranges:
        if merged and begin - merged[-1][1] < 0.05:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return merged


def quality_settings(quality: str) -> tuple[int, str]:
    """Return (crf, x264 preset) for a delivery preset name."""
    return _QUALITY.get(quality, _QUALITY["standard"])


def output_size(aspect: Aspect, info: ProbeInfo) -> tuple[int, int]:
    """Pixel size of the rendered frame; even dimensions for yuv420p."""
    if aspect in _ASPECT_SIZES:
        return _ASPECT_SIZES[aspect]
    width = info.width or 0
    height = info.height or 0
    return (max(2, width - width % 2), max(2, height - height % 2))


def _visual_args(
    aspect: Aspect,
    fit: Fit,
    info: ProbeInfo,
    subtitle_name: str | None,
) -> list[str]:
    """Build the video filter chain for an aspect mode, optional subtitles.

    crop  = fill the target frame and cut the overflow (good when the subject
            is centred).
    blur  = keep the whole source picture, with a blurred copy filling the
            letterbox (the usual short-video fallback).
    Both paths burn subtitles in the same encode, so text is not a second
    generation over an already compressed picture.
    """
    if not info.has_video:
        return []
    if aspect in _ASPECT_SIZES:
        width, height = _ASPECT_SIZES[aspect]
        if fit == "blur":
            graph = (
                f"[0:v]split=2[bg][fg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=40:2,eq=brightness=-0.08[bg2];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
                f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2"
            )
            if subtitle_name:
                graph += f"[v0];[v0]subtitles={subtitle_name}[v]"
            else:
                graph += "[v]"
            return ["-filter_complex", graph, "-map", "[v]"]
        chain = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]
        if subtitle_name:
            chain.append(f"subtitles={subtitle_name}")
        return ["-vf", ",".join(chain)]

    chain: list[str] = []
    if info.width and info.height and (info.width % 2 or info.height % 2):
        chain.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if subtitle_name:
        chain.append(f"subtitles={subtitle_name}")
    return ["-vf", ",".join(chain)] if chain else []


def render_clip(
    src: str | Path,
    dst: str | Path,
    start: float,
    end: float,
    *,
    aspect: Aspect = "source",
    fit: Fit = "crop",
    quality: str = "standard",
    crf: int | None = None,
    preset: str | None = None,
    subtitles: list[tuple[float, float, str]] | None = None,
    subtitle_style: str = "clean",
    edge_fade: float = 0.0,
    info: ProbeInfo | None = None,
) -> Path:
    """Frame-accurate cut, encoded once, with optional burned-in subtitles.

    `subtitles` uses source timestamps; entries are clipped to this slice
    before being laid out on the part's local timeline.
    """
    if end <= start:
        raise MediaError(f"invalid range: {start} -> {end}")
    src = Path(src).resolve()
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    info = info or probe(src)

    use_crf, use_preset = quality_settings(quality)
    if crf is not None:
        use_crf = crf
    if preset is not None:
        use_preset = preset

    subtitle_name: str | None = None
    ass_path: Path | None = None
    if subtitles and info.has_video:
        entries = span_entries(subtitles, start, end)
        if entries:
            width, height = output_size(aspect, info)
            subtitle_name = f"{dst.stem}.ass"
            ass_path = dst.parent / subtitle_name
            ass_path.write_text(
                ass_document(entries, width, height, style=subtitle_style),
                encoding="utf-8",
            )

    ffmpeg = _require("ffmpeg")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
    ]
    visual = _visual_args(aspect, fit, info, subtitle_name)
    cmd += visual
    if "-map" not in visual and info.has_video:
        cmd += ["-map", "0:v:0?"]
    if info.has_audio:
        cmd += ["-map", "0:a:0?"]
    if info.has_video:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            use_preset,
            "-crf",
            str(use_crf),
            "-pix_fmt",
            "yuv420p",
        ]
    if info.has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        fade = min(edge_fade, duration / 4) if edge_fade > 0 else 0.0
        if fade > 0.001:
            cmd += [
                "-af",
                f"afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={duration - fade:.3f}:d={fade:.3f}",
            ]
    cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero", dst.name]

    try:
        # cwd = output dir keeps the subtitles= filter on a bare filename, so
        # libass never sees a Windows drive colon in the filter options.
        _run(cmd, cwd=dst.parent)
    finally:
        if ass_path is not None:
            ass_path.unlink(missing_ok=True)
    return dst


def render_ranges(
    src: str | Path,
    dst: str | Path,
    ranges: list[tuple[float, float]] | list[list[float]],
    *,
    aspect: Aspect = "source",
    fit: Fit = "crop",
    quality: str = "standard",
    crf: int | None = None,
    preset: str | None = None,
    subtitles: list[tuple[float, float, str]] | None = None,
    subtitle_style: str = "clean",
    edge_fade: float = 0.008,
) -> Path:
    """Render one clip made of several kept ranges, then concat losslessly.

    Every part is encoded once (with subtitles already burned), and the
    concatenated result gets a loudness-only pass that copies the video stream,
    so nothing is re-compressed twice.
    """
    dst = Path(dst)
    cleaned = [(float(a), float(b)) for a, b in ranges if float(b) - float(a) > 0.05]
    if not cleaned:
        raise MediaError("no ranges to render")
    info = probe(src)
    common = {
        "aspect": aspect,
        "fit": fit,
        "quality": quality,
        "crf": crf,
        "preset": preset,
        "subtitles": subtitles,
        "subtitle_style": subtitle_style,
        "edge_fade": edge_fade,
        "info": info,
    }
    if len(cleaned) == 1:
        render_clip(src, dst, cleaned[0][0], cleaned[0][1], **common)
        normalize_loudness(dst)
        return dst

    parts_dir = dst.parent / f".{dst.stem}_parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)

    try:
        for index, (start, end) in enumerate(cleaned):
            render_clip(
                src,
                parts_dir / f"part_{index:03d}.mp4",
                start,
                end,
                **common,
            )
        (parts_dir / "list.txt").write_text(
            "\n".join(f"file 'part_{i:03d}.mp4'" for i in range(len(cleaned))),
            encoding="utf-8",
        )
        ffmpeg = _require("ffmpeg")
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                "list.txt",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(dst.resolve()),
            ],
            cwd=parts_dir,
        )
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    normalize_loudness(dst)
    return dst


def normalize_loudness(
    video: str | Path,
    *,
    target: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> Path:
    """Two-pass EBU R128 normalisation with the video stream copied.

    Platforms normalise uploads around -14 LUFS, so clips cut from a quiet
    recording sound small next to everything else. Pass one measures, pass two
    applies the gain the first pass computed; `-c:v copy` means no extra
    generation loss.
    """
    video = Path(video)
    ffmpeg = _require("ffmpeg")
    info = probe(video)
    if not info.has_audio:
        return video

    loudness = f"loudnorm=I={target}:TP={true_peak}:LRA={lra}"
    measure = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-af",
            f"{loudness}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )
    text = measure.stderr.decode("utf-8", "replace")
    match = _LOUDNESS_JSON.search(text)
    if measure.returncode != 0 or not match:
        return video
    try:
        data = json.loads(match.group(0))
        measured = (
            f":measured_I={data['input_i']}:measured_TP={data['input_tp']}"
            f":measured_LRA={data['input_lra']}:measured_thresh={data['input_thresh']}"
            f":offset={data['target_offset']}"
        )
    except (json.JSONDecodeError, KeyError):
        return video

    output = video.with_name(f"{video.stem}.norm{video.suffix}")
    try:
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                video.name,
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-c:v",
                "copy",
                "-af",
                loudness + measured,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                output.name,
            ],
            cwd=video.parent,
        )
        os.replace(output, video)
    except MediaError:
        output.unlink(missing_ok=True)
    return video

def burn_subtitles(
    video: str | Path,
    srt_text: str,
    *,
    style: str = "clean",
    crf: int = 20,
) -> Path:
    """Burn an SRT into the video in place (second pass, audio is copied).

    Paths are relative + cwd so the `subtitles=` filter never sees a Windows
    drive colon, which libass/ffmpeg would otherwise parse as an option.
    """
    video = Path(video)
    if not srt_text.strip():
        return video
    srt_path = video.with_name(f"{video.stem}.burn.srt")
    srt_path.write_text(srt_text, encoding="utf-8")
    output = video.with_name(f"{video.stem}.sub.mp4")
    force_style = _SUBTITLE_STYLES.get(style, _SUBTITLE_STYLES["clean"])
    try:
        ffmpeg = _require("ffmpeg")
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                video.name,
                "-vf",
                f"subtitles={srt_path.name}:charenc=UTF-8:force_style='{force_style}'",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                output.name,
            ],
            cwd=video.parent,
        )
        os.replace(output, video)
    finally:
        srt_path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
    return video
