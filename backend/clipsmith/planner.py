"""Clip planning: turn a transcript into a small set of cut candidates.

Two strategies share one output shape:

* `rules`  - deterministic, offline, always available. Used as the fallback.
* `llm`    - any OpenAI-compatible model. The model only ever returns
             *sentence id ranges*, never timestamps: ids are validated against
             the transcript, which removes the classic "model hallucinated a
             timecode" failure mode.

Both snap cuts to sentence boundaries and add a small pre-roll/post-roll so
clips do not start mid-syllable.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from .models import Clip, Sentence, Transcript

PlanMode = Literal["rules", "llm"]

# Hard ceiling for agent-supplied ranges. The system prompt asks for at most 6,
# but the prompt is a soft guide — this is the enforced bound, so one runaway
# tool call can never queue an unbounded serial render batch.
MAX_AGENT_CLIPS = 12

_KEYWORDS = (
    "结论", "其实", "建议", "为什么", "怎么", "第一", "记住", "免费", "错误",
    "方法", "提升", "测试", "数据", "关键", "本质", "核心", "但是", "所以",
)
_FILLER_PREFIXES = ("然后", "那个", "就是", "所以说", "嗯", "呃")


@dataclass
class PlanOptions:
    mode: PlanMode = "rules"
    limit: int = 8
    min_seconds: float = 10.0
    max_seconds: float = 60.0
    pad_start: float = 0.12
    pad_end: float = 0.18
    # Remove dead air inside clips (ffmpeg silencedetect). auto-editor's default
    # cut, applied here so cuts still land on sentence boundaries.
    trim_silence: bool = True
    silence_keep: float = 0.12
    # Free-form direction for the LLM ("只挑有争议的观点" / "每条 20 秒内").
    instruction: str | None = None


def plan(
    transcript: Transcript,
    options: PlanOptions,
    *,
    llm: "LLMConfig | None" = None,
) -> tuple[list[Clip], str | None]:
    """Return (clips, warning). A warning means the LLM path fell back to rules."""
    if options.mode == "llm" and llm and llm.api_key:
        try:
            clips = _plan_with_llm(transcript, options, llm)
            if clips:
                return _finalize(clips, transcript, options), None
            warning = "LLM 返回的片段为空，已回退到规则选段"
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the job
            warning = f"LLM 选段失败（{type(exc).__name__}: {exc}），已回退到规则选段"
    else:
        warning = None
        if options.mode == "llm":
            warning = "未配置 LLM API Key，已使用规则选段"

    clips = _plan_with_rules(transcript, options)
    return _finalize(clips, transcript, options), warning


def clips_from_sentences(
    transcript: Transcript,
    sentence_ids: list[int],
    options: PlanOptions,
) -> list[Clip]:
    """Manual selection: contiguous runs of sentences become clips."""
    by_id = {s.id: s for s in transcript.sentences}
    chosen = sorted({sid for sid in sentence_ids if sid in by_id})
    if not chosen:
        return []

    runs: list[list[int]] = []
    current = [chosen[0]]
    for sid in chosen[1:]:
        if sid == current[-1] + 1:
            current.append(sid)
        else:
            runs.append(current)
            current = [sid]
    runs.append(current)

    clips: list[Clip] = []
    for run in runs:
        sentences = [by_id[i] for i in run]
        start, end = sentences[0].start, sentences[-1].end
        if end - start < 1.0:
            continue
        clips.append(
            Clip(
                id=uuid.uuid4().hex[:6],
                start=start,
                end=end,
                sentence_ids=run,
                hook=_hook(sentences[0].text),
                title=_hook(sentences[0].text),
                score=_score(sentences, start, end, options),
                reason="手动选择",
            )
        )
    return _finalize(clips, transcript, options)


def clips_from_specs(
    transcript: Transcript,
    specs: list[dict],
    options: PlanOptions,
) -> list[Clip]:
    """Build clips from explicit sentence ranges supplied by the agent.

    The agent never talks in timestamps; it only picks sentence ids, which are
    validated here before any media is touched. At most MAX_AGENT_CLIPS ranges
    are accepted, and a spec with unparseable fields is skipped instead of
    failing the whole selection.
    """
    by_id = {sentence.id: sentence for sentence in transcript.sentences}
    clips: list[Clip] = []
    for spec in specs[:MAX_AGENT_CLIPS]:
        if not isinstance(spec, dict):
            continue
        try:
            first = int(spec.get("from_sentence"))
            last = int(spec.get("to_sentence"))
        except (TypeError, ValueError):
            continue
        if first > last:
            first, last = last, first
        ids = [index for index in range(first, last + 1) if index in by_id]
        if not ids:
            continue
        sentences = [by_id[index] for index in ids]
        start, end = sentences[0].start, sentences[-1].end
        if end - start < 1.0:
            continue
        try:
            score = _clamp(float(spec.get("score", 0.7) or 0.7))
        except (TypeError, ValueError):
            score = 0.5
        hook = str(spec.get("hook") or _hook(sentences[0].text))[:60]
        clips.append(
            Clip(
                id=uuid.uuid4().hex[:6],
                start=start,
                end=end,
                sentence_ids=ids,
                hook=hook,
                title=str(spec.get("title") or hook)[:40],
                caption=str(spec.get("caption") or "")[:120],
                tags=_clean_tags(spec.get("tags")),
                score=score,
                reason=str(spec.get("reason") or "AI 选段")[:200],
            )
        )
    return _finalize(clips, transcript, options)


# -- rules -----------------------------------------------------------------


def _plan_with_rules(transcript: Transcript, options: PlanOptions) -> list[Clip]:
    sentences = transcript.sentences
    if not sentences:
        return []

    candidates: list[Clip] = []
    window: list[Sentence] = []

    def flush() -> None:
        if not window:
            return
        start, end = window[0].start, window[-1].end
        if end - start < options.min_seconds and candidates:
            # Too short on its own: merge into the previous candidate.
            previous = candidates[-1]
            if end - previous.start <= options.max_seconds:
                previous.end = end
                previous.sentence_ids += [s.id for s in window]
                previous.score = _score(
                    [s for s in sentences if s.id in set(previous.sentence_ids)],
                    previous.start,
                    previous.end,
                    options,
                )
                window.clear()
                return
        candidates.append(
            Clip(
                id=uuid.uuid4().hex[:6],
                start=start,
                end=end,
                sentence_ids=[s.id for s in window],
                hook=_hook(window[0].text),
                title=_hook(window[0].text),
                score=_score(window, start, end, options),
                reason=_rule_reason(window, start, end, options),
            )
        )
        window.clear()

    for sentence in sentences:
        if window and sentence.end - window[0].start > options.max_seconds:
            flush()
        window.append(sentence)
        if sentence.end - window[0].start >= _target_length(options):
            flush()
    flush()

    candidates.sort(key=lambda c: c.score, reverse=True)
    picked = candidates[: options.limit]
    picked.sort(key=lambda c: c.start)
    return picked


def _target_length(options: PlanOptions) -> float:
    return max(options.min_seconds, min(options.max_seconds, 30.0))


def _score(
    sentences: list[Sentence],
    start: float,
    end: float,
    options: PlanOptions,
) -> float:
    duration = max(0.1, end - start)
    text = "".join(s.text for s in sentences)

    length_score = 1.0 - min(1.0, abs(duration - 28.0) / 30.0)
    density = sum(text.count(k) for k in _KEYWORDS) / max(1.0, duration / 10.0)
    keyword_score = min(1.0, density / 1.5)
    pacing = len(sentences) / max(1.0, duration / 10.0)
    pacing_score = min(1.0, pacing / 4.0)
    penalty = 0.15 if text.startswith(_FILLER_PREFIXES) else 0.0
    completeness = 0.1 if text.endswith(("。", "？", "！", ".", "?", "!")) else 0.0

    raw = 0.45 * length_score + 0.3 * keyword_score + 0.15 * pacing_score + completeness - penalty
    return round(max(0.05, min(0.99, raw)), 2)


def _rule_reason(
    sentences: list[Sentence],
    start: float,
    end: float,
    options: PlanOptions,
) -> str:
    duration = end - start
    return (
        f"规则选段：时长 {duration:.0f}s，"
        f"{len(sentences)} 句，关键词密度 {_score(sentences, start, end, options):.2f}"
    )


def _hook(text: str, limit: int = 28) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


# -- LLM -------------------------------------------------------------------


@dataclass
class LLMConfig:
    api_key: str
    model: str
    base_url: str | None = None
    timeout: float = 90.0


_SYSTEM_PROMPT = """你是一位短视频剪辑师。用户会给你一段视频的分句转写稿，
每行格式为：[句子id] 开始秒-结束秒 内容。

请挑出适合独立成片的片段，要求：
1. 每个片段是 3~8 个连续句子，总时长在 {min_s:.0f}~{max_s:.0f} 秒之间；
2. 有完整的观点或故事，开头能抓住注意力，结尾不悬空；
3. 片段之间不要重叠，按吸引力排序；
4. 最多 {limit} 个片段。

只输出 JSON，不要解释，格式：
{{"clips":[{{"from":3,"to":7,"title":"6-12字标题","hook":"一句抓人的开场钩子","score":0.0,"reason":"为什么这段能独立传播","caption":"可直接发布的文案，40字内","tags":["标签1","标签2"]}}]}}
其中 from/to 是句子 id（包含两端），score 是 0~1 的小数，tags 给 2~4 个不带 # 的短标签。{extra}"""


def _plan_with_llm(
    transcript: Transcript,
    options: PlanOptions,
    llm: LLMConfig,
) -> list[Clip]:
    from openai import OpenAI

    extra = ""
    if options.instruction:
        prefix = "用户额外要求（优先满足，但不得突破时长和句子连续性约束）："
        extra = "\n\n" + prefix + options.instruction

    rows = "\n".join(
        f"[{s.id}] {s.start:.1f}-{s.end:.1f} {('[' + s.speaker + '] ') if s.speaker else ''}{s.text}"
        for s in transcript.sentences
    )
    client = OpenAI(
        api_key=llm.api_key,
        base_url=llm.base_url or None,
        timeout=llm.timeout,
    )
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_PROMPT.format(
                min_s=options.min_seconds,
                max_s=options.max_seconds,
                limit=options.limit,
                extra=extra,
            ),
        },
        {"role": "user", "content": f"转写稿：\n{rows}"},
    ]
    try:
        response = client.chat.completions.create(
            model=llm.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2500,
            response_format={"type": "json_object"},
        )
    except Exception:  # noqa: BLE001 - some providers reject response_format
        response = client.chat.completions.create(
            model=llm.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2500,
        )

    payload = _loads_json(response.choices[0].message.content or "")
    raw_clips = payload.get("clips") if isinstance(payload, dict) else payload
    if not isinstance(raw_clips, list):
        raise ValueError("LLM response has no clips array")

    by_id = {s.id: s for s in transcript.sentences}
    clips: list[Clip] = []
    for item in raw_clips:
        if not isinstance(item, dict):
            continue
        try:
            first, last = int(item["from"]), int(item["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if first > last:
            first, last = last, first
        ids = [i for i in range(first, last + 1) if i in by_id]
        if not ids or ids != list(range(ids[0], ids[-1] + 1)):
            continue
        sentences = [by_id[i] for i in ids]
        start, end = sentences[0].start, sentences[-1].end
        duration = end - start
        if duration < options.min_seconds * 0.6 or duration > options.max_seconds * 1.5:
            continue
        clips.append(
            Clip(
                id=uuid.uuid4().hex[:6],
                start=start,
                end=end,
                sentence_ids=ids,
                hook=str(item.get("hook") or _hook(sentences[0].text))[:60],
                title=str(item.get("title") or item.get("hook") or _hook(sentences[0].text))[:40],
                caption=str(item.get("caption") or "")[:120],
                tags=_clean_tags(item.get("tags")),
                score=round(_clamp(float(item.get("score", 0.5) or 0.5)), 2),
                reason=str(item.get("reason") or "LLM 选段")[:200],
            )
        )
    return _drop_overlaps(clips)[: options.limit]


def _drop_overlaps(clips: list[Clip], max_overlap: float = 0.25) -> list[Clip]:
    kept: list[Clip] = []
    for clip in sorted(clips, key=lambda c: c.score, reverse=True):
        if all(_overlap_ratio(clip, other) <= max_overlap for other in kept):
            kept.append(clip)
    return kept


def _overlap_ratio(a: Clip, b: Clip) -> float:
    overlap = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    shortest = max(0.1, min(a.end - a.start, b.end - b.start))
    return overlap / shortest


def _loads_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _clamp(value: float) -> float:
    if math.isnan(value):
        return 0.5
    return max(0.0, min(1.0, value))


def _finalize(clips: list[Clip], transcript: Transcript, options: PlanOptions) -> list[Clip]:
    """Apply pre/post-roll, then clamp neighbours so padding never overlaps."""
    limit = max(0.0, transcript.duration)
    ordered = sorted(clips, key=lambda c: c.start)
    for clip in ordered:
        clip.start = round(max(0.0, clip.start - options.pad_start), 3)
        clip.end = round(min(limit, clip.end + options.pad_end), 3)

    result: list[Clip] = []
    for clip in ordered:
        if result and clip.start < result[-1].end:
            clip.start = result[-1].end
        if clip.end - clip.start < 1.0:
            continue
        result.append(clip)
    return result

def apply_silence_trim(
    clips: list[Clip],
    silences: list[tuple[float, float]],
    options: PlanOptions,
) -> None:
    """Split each clip around detected dead air, keeping a short breath.

    `clip.start/end` stay as the source span (what the editor shows); the
    playable result lives in `clip.ranges`. Rendering concatenates the ranges.
    """
    if not silences:
        return
    for clip in clips:
        base = clip.ranges or [[clip.start, clip.end]]
        pieces: list[list[float]] = []
        removed_count = 0
        removed_seconds = 0.0

        for begin, end in base:
            cuts: list[tuple[float, float]] = []
            for silence_start, silence_end in silences:
                if silence_end <= begin or silence_start >= end:
                    continue
                cut_start = max(begin, silence_start + options.silence_keep)
                cut_end = min(end, silence_end - options.silence_keep)
                if cut_end - cut_start >= 0.12:
                    cuts.append((cut_start, cut_end))

            cursor = begin
            for cut_start, cut_end in sorted(cuts):
                if cut_start > cursor + 0.05:
                    pieces.append([cursor, cut_start])
                cursor = max(cursor, cut_end)
                removed_count += 1
                removed_seconds += cut_end - cut_start
            if cursor < end - 0.05:
                pieces.append([cursor, end])

        if not pieces:
            continue
        clip.ranges = [[round(a, 3), round(b, 3)] for a, b in pieces]
        clip.removed_pauses = removed_count
        clip.removed_seconds = round(removed_seconds, 2)

def _clean_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for raw in value:
        tag = str(raw).strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag[:16])
        if len(tags) >= 4:
            break
    return tags
