"""Mock engine: synthetic but time-aligned transcript.

Purpose: develop and demo the editor without FunASR, torch, or a GPU. It is
also what the test suite uses, so every UI path stays runnable on any machine.
"""

from __future__ import annotations

from pathlib import Path

from ..media import probe
from ..models import Sentence, Transcript

_LINES = [
    "今天想跟你聊一个特别容易被忽略的问题。",
    "很多人做内容的时候，第一反应是先把画面拍漂亮。",
    "但真正决定完播率的，其实是前五秒钟有没有给出理由。",
    "你可以试试把结论放在最前面。",
    "然后用一个具体的例子把它撑起来。",
    "比如说，同样一句话，换一种说法，观众停留的时长完全不一样。",
    "这不是玄学，是注意力在起作用。",
    "我们做过一次测试，把开场从背景介绍改成直接给结果。",
    "完播率大概提升了一点七倍。",
    "所以我的建议是，先写结尾，再写开头。",
    "最后再把中间那些不必要的过渡删掉。",
    "你会发现整条片子紧了很多。",
    "如果这条对你有用，记得点个收藏。",
    "我们下期再见。",
]


class MockEngine:
    name = "mock"

    def __init__(self, seconds_per_sentence: float = 3.0) -> None:
        self.seconds_per_sentence = seconds_per_sentence

    def transcribe(self, media_path: Path, work_dir: Path) -> Transcript:
        info = probe(media_path)
        duration = max(info.duration, 1.0)
        sentences: list[Sentence] = []
        cursor = 0.0
        index = 0
        while cursor < duration - 0.2:
            end = min(duration, cursor + self.seconds_per_sentence)
            sentences.append(
                Sentence(
                    id=index,
                    start=round(cursor, 3),
                    end=round(end, 3),
                    text=_LINES[index % len(_LINES)],
                    speaker="S1" if index % 2 == 0 else "S2",
                )
            )
            cursor = end
            index += 1
        return Transcript(
            language="zh",
            duration=duration,
            sentences=sentences,
            engine=self.name,
            meta={"note": "synthetic transcript for UI development"},
        )