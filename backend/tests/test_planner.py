from __future__ import annotations

import pytest

from clipsmith.models import Sentence, Transcript
from clipsmith.planner import PlanOptions, clips_from_sentences, plan


def make_transcript(count: int = 12, step: float = 3.0) -> Transcript:
    sentences = [
        Sentence(
            id=i,
            start=i * step,
            end=(i + 1) * step,
            text=f"这是第{i}句，结论很重要，建议记住这个测试数据。",
        )
        for i in range(count)
    ]
    return Transcript(language="zh", duration=count * step, sentences=sentences)


def test_rules_plan_respects_bounds_and_overlap() -> None:
    transcript = make_transcript()
    options = PlanOptions(mode="rules", limit=5, min_seconds=8, max_seconds=30)
    clips, warning = plan(transcript, options)

    assert warning is None
    assert clips
    for clip in clips:
        assert clip.end - clip.start <= options.max_seconds + 1.0
        assert 0 <= clip.start < clip.end <= transcript.duration
        assert clip.sentence_ids == list(range(clip.sentence_ids[0], clip.sentence_ids[-1] + 1))

    ordered = sorted(clips, key=lambda c: c.start)
    for left, right in zip(ordered, ordered[1:]):
        assert left.end <= right.start + 0.01


def test_manual_selection_splits_into_runs() -> None:
    transcript = make_transcript()
    clips = clips_from_sentences(transcript, [0, 1, 2, 5, 6], PlanOptions())
    assert len(clips) == 2
    assert clips[0].sentence_ids == [0, 1, 2]
    assert clips[1].sentence_ids == [5, 6]
    # pre-roll and post-roll are applied
    assert clips[0].start < 0.0 + 1e-9 or clips[0].start == 0.0
    assert clips[1].end > transcript.sentences[6].end


def test_manual_selection_ignores_unknown_ids() -> None:
    transcript = make_transcript()
    assert clips_from_sentences(transcript, [99, 100], PlanOptions()) == []


def test_llm_without_key_falls_back_to_rules() -> None:
    transcript = make_transcript()
    clips, warning = plan(transcript, PlanOptions(mode="llm"))
    assert clips
    assert warning is not None and "API Key" in warning


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_limit_is_honored(limit: int) -> None:
    transcript = make_transcript(30, 2.0)
    clips, _ = plan(transcript, PlanOptions(mode="rules", limit=limit))
    assert len(clips) <= limit

def test_silence_trim_splits_ranges() -> None:
    from clipsmith.planner import apply_silence_trim

    transcript = make_transcript(10, 3.0)
    options = PlanOptions(mode="manual", trim_silence=True, silence_keep=0.1)
    clips = clips_from_sentences(transcript, [0, 1, 2, 3], options)
    clip = clips[0]
    original = clip.end - clip.start

    apply_silence_trim([clip], [(2.0, 4.0)], options)

    assert clip.removed_pauses == 1
    assert len(clip.ranges) == 2
    assert clip.duration < original
    assert abs(sum(b - a for a, b in clip.ranges) - clip.duration) < 1e-6
