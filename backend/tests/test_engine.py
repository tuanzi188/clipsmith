import pytest

from clipsmith.config import Settings
from clipsmith.engine import build_engine


def test_whisper_engine_is_selectable() -> None:
    engine = build_engine(Settings(engine="whisper", whisper_model="small"))
    assert engine.name == "whisper"
    assert engine.model_size == "small"


def test_unknown_engine_lists_choices() -> None:
    with pytest.raises(ValueError) as exc:
        build_engine(Settings(engine="nope"))
    assert "whisper" in str(exc.value)