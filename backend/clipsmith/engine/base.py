"""Engine contract.

An engine only has to answer one question: "what was said, and when".
Everything downstream (selection, cutting, export) is engine-agnostic, which is
what lets the mock engine exercise the whole product without downloading a
multi-GB ASR model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Transcript


class Engine(Protocol):
    name: str

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        """Return a transcript with sentence-level, second-based timestamps."""