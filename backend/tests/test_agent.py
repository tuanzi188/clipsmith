from __future__ import annotations

from pathlib import Path

import pytest

from clipsmith.agent import ClipAgent
from clipsmith.config import Settings
from clipsmith.engine.mock import MockEngine
from clipsmith.jobs import JobStore
from clipsmith.models import Sentence, Transcript


async def _noop(job) -> None:  # noqa: ANN001
    return None


def _make_agent(tmp_path: Path) -> tuple[ClipAgent, JobStore, str]:
    settings = Settings(data_dir=tmp_path / "data", engine="mock")
    settings.frontend_dist = tmp_path / "no-dist"
    store = JobStore(settings.data_dir)
    job = store.create("talk.mp4")
    job.duration = 24.0
    job.status = "transcribed"
    job.transcript = Transcript(
        language="zh",
        duration=24.0,
        engine="mock",
        sentences=[
            Sentence(
                id=index,
                start=index * 3.0,
                end=(index + 1) * 3.0,
                text=f"第{index}句，结论很重要，建议记住。",
            )
            for index in range(8)
        ],
    )
    store.save(job)
    agent = ClipAgent(store, job.id, settings, MockEngine(), _noop)
    return agent, store, job.id


def test_select_and_update_clips_offline(tmp_path: Path) -> None:
    agent, store, job_id = _make_agent(tmp_path)

    payload, summary = agent._execute(
        "select_clips",
        {
            "clips": [
                {"from_sentence": 0, "to_sentence": 2, "title": "开场"},
                {"from_sentence": 4, "to_sentence": 5, "title": "方法"},
            ],
            "trim_silence": False,
        },
    )
    assert payload["ok"], summary
    job = store.load(job_id)
    assert len(job.clips) == 2
    first = job.clips[0]
    assert first.title == "开场"
    assert first.start == 0.0
    assert first.end == pytest.approx(9.0, abs=0.3)

    clip_id = job.clips[1].id
    payload, _ = agent._execute(
        "update_clip", {"clip_id": clip_id, "from_sentence": 5, "to_sentence": 5}
    )
    assert payload["ok"]
    job = store.load(job_id)
    updated = next(c for c in job.clips if c.id == clip_id)
    assert updated.end - updated.start == pytest.approx(3.0, abs=0.4)


def test_project_state_and_options(tmp_path: Path) -> None:
    agent, store, job_id = _make_agent(tmp_path)
    state, _ = agent._execute("get_project_state", {})
    assert state["transcript"]["sentences"] == 8

    payload, _ = agent._execute(
        "set_export_options",
        {"aspect": "9:16", "fit": "blur", "subtitles": True, "quality": "final"},
    )
    assert payload["ok"]
    job = store.load(job_id)
    assert job.export_aspect == "9:16"
    assert job.aspect_fit == "blur"
    assert job.render_quality == "final"
    assert job.export_subtitles is True

    rejected, _ = agent._execute("set_export_options", {"quality": "4k"})
    assert rejected["ok"] is False

    transcript, _ = agent._execute("get_transcript", {"query": "结论", "limit": 3})
    assert transcript["total"] == 8
    assert len(transcript["sentences"]) == 3


def test_unknown_tool_returns_error(tmp_path: Path) -> None:
    agent, _, _ = _make_agent(tmp_path)
    payload, summary = agent._execute("rm_rf", {})
    assert payload["ok"] is False
    assert "unknown tool" in payload["error"]
