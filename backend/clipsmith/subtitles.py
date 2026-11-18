"""SRT export.

Two views, following the "the timeline is the document, SRT is an export"
model: per-clip SRT that follows the kept ranges, and a combined SRT for a
multi-clip sequence.
"""

from __future__ import annotations

import os

from .models import Clip, Sentence

# ASS styles are resolution-independent: the play area is set to the actual
# output frame, and font size / margin are ratios of its height. Burning an
# SRT would make libass fall back to its 384x288 canvas, where the same style
# balloons on a 1080x1920 render and floats far from the bottom.
_ASS_STYLES = {
    "clean": {"size": 0.052, "outline": 3, "bold": False, "margin": 0.14},
    "bold": {"size": 0.064, "outline": 5, "bold": True, "margin": 0.16},
}
_DEFAULT_FONT = "Microsoft YaHei"


def _ass_time(seconds: float) -> str:
    centis = max(0, int(round(seconds * 100)))
    hours, rem = divmod(centis, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, hundredths = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{hundredths:02d}"


def _ass_text(text: str) -> str:
    # Braces open override blocks and backslashes are ASS escapes; strip both
    # rather than let a stray character swallow the rest of the line.
    cleaned = text.strip().replace("{", "(").replace("}", ")").replace("\\", "＼")
    return cleaned.replace("\r", "").replace("\n", "\\N")


def span_entries(
    sentences: list[Sentence] | list[tuple[float, float, str]],
    begin: float,
    end: float,
) -> list[tuple[float, float, str]]:
    """Sentences clipped to one kept range, with times relative to that range.

    Each rendered part is a contiguous slice of the source, so source time minus
    the slice start is exactly the part's local timeline. Cropping sentences to
    the slice means a sentence that straddles a removed pause is split across
    the two parts instead of drifting.
    """
    entries: list[tuple[float, float, str]] = []
    previous_end = 0.0
    for item in sentences:
        if isinstance(item, Sentence):
            s_start, s_end, text = item.start, item.end, item.text
        else:
            s_start, s_end, text = item
        if s_end <= begin or s_start >= end:
            continue
        start = max(s_start, begin) - begin
        stop = min(s_end, end) - begin
        if start < previous_end:
            start = previous_end + 0.01
        if stop - start < 0.08:
            continue
        entries.append((start, stop, text))
        previous_end = stop
    return entries


def ass_document(
    entries: list[tuple[float, float, str]],
    width: int,
    height: int,
    *,
    style: str = "clean",
    font: str | None = None,
) -> str:
    """Render subtitle entries as an ASS file sized for the output frame."""
    preset = _ASS_STYLES.get(style, _ASS_STYLES["clean"])
    font_name = font or os.environ.get("CLIPSMITH_SUBTITLE_FONT") or _DEFAULT_FONT
    size = max(18, int(round(height * preset["size"])))
    margin = max(24, int(round(height * preset["margin"])))
    bold = -1 if preset["bold"] else 0
    side = max(40, int(round(width * 0.06)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{size},&H00FFFFFF,&H000000FF,&H96000000,"
        f"&H64000000,{bold},0,0,0,100,100,0,0,1,{preset['outline']},0,2,"
        f"{side},{side},{margin},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(stop)},Default,,0,0,0,,"
        f"{_ass_text(text)}\n"
        for start, stop, text in entries
        if text.strip()
    )
    return header + events


def _stamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _block(index: int, start: float, end: float, text: str) -> str:
    return f"{index}\n{_stamp(start)} --> {_stamp(end)}\n{text.strip()}\n"


def to_srt(sentences: list[Sentence]) -> str:
    if not sentences:
        return ""
    lines = []
    for index, sentence in enumerate(sentences, start=1):
        lines.append(_block(index, sentence.start, max(sentence.end, sentence.start + 0.2), sentence.text))
    return "\n".join(lines)


def _map_time(value: float, ranges: list[list[float]]) -> float:
    """Map a source timestamp onto the concatenated output timeline."""
    offset = 0.0
    for begin, end in ranges:
        if value <= begin:
            return offset
        if value <= end:
            return offset + (value - begin)
        offset += end - begin
    return offset


def _entries(
    sentences: list[Sentence],
    spans: list[list[float]],
    limit: float | None = None,
) -> list[tuple[float, float, str]]:
    total = limit if limit is not None else sum(max(0.0, stop - begin) for begin, stop in spans)
    entries: list[tuple[float, float, str]] = []
    previous_end = 0.0
    for sentence in sentences:
        if not any(sentence.end > begin and sentence.start < stop for begin, stop in spans):
            continue
        start = _map_time(sentence.start, spans)
        end = _map_time(sentence.end, spans)
        # SRT must be strictly monotonic: JianYing rejects overlapping subtitles,
        # and sentences that straddle a removed pause can map out of order.
        if start < previous_end:
            start = previous_end + 0.001
        if start >= total - 0.05:
            continue
        if end > total:
            end = total
        if end - start < 0.2:
            end = min(total, start + 0.2)
        if end - start <= 0.02:
            continue
        entries.append((start, end, sentence.text))
        previous_end = end
    return entries


def clip_srt(
    sentences: list[Sentence],
    ranges: list[list[float]] | None,
    start: float,
    end: float,
) -> str:
    spans = ranges or [[start, end]]
    entries = _entries(sentences, spans)
    if not entries:
        return ""
    return "\n".join(
        _block(index, begin, finish, text)
        for index, (begin, finish, text) in enumerate(entries, start=1)
    )


def timeline_srt(sentences: list[Sentence], clips: list[Clip]) -> str:
    """One SRT for a multi-clip sequence, as it will play back to back."""
    blocks: list[str] = []
    offset = 0.0
    index = 1
    for clip in clips:
        spans = clip.ranges or [[clip.start, clip.end]]
        for begin, finish, text in _entries(sentences, spans, limit=clip.duration):
            blocks.append(_block(index, offset + begin, offset + finish, text))
            index += 1
        offset += sum(max(0.0, stop - start) for start, stop in spans)
    return "\n".join(blocks)