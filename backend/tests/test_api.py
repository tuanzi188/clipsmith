from __future__ import annotations

import math
import shutil
import struct
import time
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for the end-to-end test",
)

from fastapi.testclient import TestClient  # noqa: E402

from clipsmith.api import create_app  # noqa: E402
from clipsmith.config import Settings  # noqa: E402


def _make_wav(path: Path, seconds: float = 9.0) -> bytes:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frames = bytearray()
        for i in range(int(16000 * seconds)):
            frames += struct.pack("<h", int(1500 * math.sin(2 * math.pi * 220 * i / 16000)))
        handle.writeframes(bytes(frames))
    return path.read_bytes()


def _wait(client: TestClient, job_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in statuses:
            return payload
        if payload["status"] == "error":
            raise AssertionError(payload.get("error"))
        time.sleep(0.15)
    raise AssertionError(f"job stuck in {payload['status']}")


def test_full_pipeline_with_mock_engine(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", engine="mock")
    settings.frontend_dist = tmp_path / "no-dist"
    app = create_app(settings)
    with TestClient(app) as client:
        wav_bytes = _make_wav(tmp_path / "talk.wav")
        response = client.post(
            "/api/jobs", files={"file": ("talk.wav", wav_bytes, "audio/wav")}
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["duration"] == pytest.approx(9.0, abs=0.2)

        assert client.post(f"/api/jobs/{job['id']}/transcribe").status_code == 200
        job = _wait(client, job["id"], {"transcribed"})
        assert len(job["transcript"]["sentences"]) == 3

        plan_response = client.post(
            f"/api/jobs/{job['id']}/plan",
            json={"mode": "manual", "sentence_ids": [0, 1]},
        )
        assert plan_response.status_code == 200, plan_response.text
        job = _wait(client, job["id"], {"planned"})
        assert len(job["clips"]) == 1
        clip = job["clips"][0]

        assert client.post(f"/api/jobs/{job['id']}/render").status_code == 200
        job = _wait(client, job["id"], {"ready"})
        assert job["clips"][0]["status"] == "ready"
        assert job["clips"][0]["file"]

        media = client.get(f"/api/jobs/{job['id']}/clips/{clip['id']}/file")
        assert media.status_code == 200
        assert len(media.content) > 1000


def test_upload_rejects_unknown_type(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", engine="mock")
    settings.frontend_dist = tmp_path / "no-dist"
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/jobs", files={"file": ("x.exe", b"nope", "application/octet-stream")})
        assert response.status_code == 400


def test_plan_requires_transcript(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", engine="mock")
    settings.frontend_dist = tmp_path / "no-dist"
    with TestClient(create_app(settings)) as client:
        wav = _make_wav(tmp_path / "a.wav", 3)
        job = client.post("/api/jobs", files={"file": ("a.wav", wav, "audio/wav")}).json()
        response = client.post(f"/api/jobs/{job['id']}/plan", json={"mode": "rules"})
        assert response.status_code == 409

def test_upload_preserves_utf8_filename(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", engine="mock")
    settings.frontend_dist = tmp_path / "no-dist"
    with TestClient(create_app(settings)) as client:
        wav = _make_wav(tmp_path / "clip.wav", 2)
        response = client.post(
            "/api/jobs", files={"file": ("中文素材.wav", wav, "audio/wav")}
        )
        assert response.status_code == 200, response.text
        assert response.json()["filename"] == "中文素材.wav"
