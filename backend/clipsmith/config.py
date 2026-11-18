"""Runtime configuration, all via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _load_env_file() -> None:
    """Read backend/.env once, without overriding real environment variables.

    A local .env keeps the magpie gateway credentials out of the shell history;
    the parser is deliberately tiny so the package keeps zero dependencies.
    """
    for path in (_REPO_ROOT / "backend" / ".env", Path.cwd() / ".env"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        break


@dataclass
class Settings:
    # Storage
    data_dir: Path = field(
        default_factory=lambda: Path(
            _env("CLIPSMITH_DATA", str(_REPO_ROOT / "backend" / "data"))
        ).resolve()
    )
    max_upload_mb: int = field(default_factory=lambda: int(_env("CLIPSMITH_MAX_UPLOAD_MB", "4096")))

    # Engine: "mock" (no models), "funasr" (direct FunASR), "funclip" (vendored FunClip)
    engine: str = field(default_factory=lambda: _env("CLIPSMITH_ENGINE", "mock"))
    asr_model: str = field(default_factory=lambda: _env("CLIPSMITH_ASR_MODEL", "paraformer-zh"))
    asr_language: str = field(default_factory=lambda: _env("CLIPSMITH_ASR_LANG", "zh"))
    asr_hotwords: str = field(default_factory=lambda: _env("CLIPSMITH_HOTWORDS", ""))
    asr_device: str = field(default_factory=lambda: _env("CLIPSMITH_ASR_DEVICE", "cpu"))
    asr_speaker: bool = field(
        default_factory=lambda: _env("CLIPSMITH_ASR_SPK", "0").lower() in ("1", "true", "yes")
    )
    # faster-whisper: the lightest truthful ASR path (CTranslate2, int8 on CPU).
    whisper_model: str = field(default_factory=lambda: _env("CLIPSMITH_WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: _env("CLIPSMITH_WHISPER_DEVICE", "cpu"))
    whisper_compute: str = field(default_factory=lambda: _env("CLIPSMITH_WHISPER_COMPUTE", "int8"))
    # Optional path to a FunClip checkout; only used by the "funclip" engine.
    funclip_path: str = field(default_factory=lambda: _env("CLIPSMITH_FUNCLIP_PATH", "vendor/FunClip"))

    # LLM selection (any OpenAI-compatible endpoint)
    llm_model: str = field(default_factory=lambda: _env("CLIPSMITH_LLM_MODEL", "deepseek-chat"))
    llm_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    llm_timeout: float = field(default_factory=lambda: float(_env("CLIPSMITH_LLM_TIMEOUT", "90")))

    # Clip constraints (seconds)
    min_clip_seconds: float = field(default_factory=lambda: float(_env("CLIPSMITH_MIN_CLIP", "10")))
    max_clip_seconds: float = field(default_factory=lambda: float(_env("CLIPSMITH_MAX_CLIP", "60")))

    # Frontend build directory, served when present.
    frontend_dist: Path = field(
        default_factory=lambda: Path(
            _env("CLIPSMITH_FRONTEND_DIST", str(_REPO_ROOT / "frontend" / "dist"))
        ).resolve()
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


_load_env_file()

settings = Settings()