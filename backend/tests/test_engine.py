import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from clipsmith.config import Settings
from clipsmith.engine import build_engine
from clipsmith.engine.openai_asr import OpenAIASREngine, segments_to_sentences


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


def test_whisper_engine_is_selectable() -> None:
    engine = build_engine(Settings(engine="whisper", whisper_model="small"))
    assert engine.name == "whisper"
    assert engine.model_size == "small"


def test_cloud_engine_is_selectable() -> None:
    engine = build_engine(
        Settings(
            engine="openai-asr",
            cloud_asr_base_url="https://api.groq.com/openai/v1/",
            cloud_asr_model="whisper-large-v3",
        )
    )
    assert engine.name == "openai-asr"
    assert engine.base_url == "https://api.groq.com/openai/v1"  # trailing slash trimmed


def test_cloud_engine_requires_config() -> None:
    with pytest.raises(ValueError) as exc:
        build_engine(Settings(engine="openai-asr"))
    assert "base URL" in str(exc.value)


def test_segments_accept_objects_and_dicts() -> None:
    sentences = segments_to_sentences(
        [
            _Segment(1.0, 2.5, " 第一句 "),
            {"start": 3.0, "end": 4.2, "text": "第二句"},
            {"start": 5.0, "end": 5.0, "text": "空区间"},
            {"start": 6.0, "end": 7.0, "text": "   "},
        ]
    )
    assert [(s.id, s.start, s.end, s.text) for s in sentences] == [
        (0, 1.0, 2.5, "第一句"),
        (1, 3.0, 4.2, "第二句"),
    ]


def test_unknown_engine_lists_choices() -> None:
    with pytest.raises(ValueError) as exc:
        build_engine(Settings(engine="nope"))
    assert "whisper" in str(exc.value)


def _make_wav(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
            "-ar", "16000", "-ac", "1", str(path),
        ],
        check=True,
    )
    return path


class _TranscriptionHandler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "has_model": b"whisper-test" in body,
            "bytes": len(body),
        }
        payload = {
            "text": "你好世界",
            "language": "zh",
            "duration": 1.5,
            "segments": [
                {"id": 0, "start": 0.0, "end": 0.8, "text": "你好"},
                {"id": 1, "start": 0.8, "end": 1.5, "text": "世界"},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_cloud_engine_sends_audio_and_reads_segments(tmp_path: Path) -> None:
    wav = _make_wav(tmp_path / "a.wav")
    server = HTTPServer(("127.0.0.1", 0), _TranscriptionHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        engine = OpenAIASREngine(
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            api_key="test-key",
            model="whisper-test",
            language="zh",
        )
        transcript = engine.transcribe(wav, tmp_path)
    finally:
        server.shutdown()

    assert [s.text for s in transcript.sentences] == ["你好", "世界"]
    assert transcript.duration == 1.5
    assert _TranscriptionHandler.seen["path"].endswith("/audio/transcriptions")
    assert _TranscriptionHandler.seen["auth"] == "Bearer test-key"
    assert _TranscriptionHandler.seen["has_model"] is True