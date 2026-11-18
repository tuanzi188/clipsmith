from clipsmith.models import Sentence
from clipsmith.subtitles import ass_document, clip_srt, span_entries, to_srt


def test_full_srt_format() -> None:
    out = to_srt([Sentence(id=0, start=0.0, end=1.5, text="第一句"), Sentence(id=1, start=61.2, end=63.0, text="第二句")])
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,500\n第一句")
    assert "2\n00:01:01,200 --> 00:01:03,000\n第二句" in out


def test_clip_srt_remaps_around_cut_pauses() -> None:
    sentences = [
        Sentence(id=0, start=0.0, end=2.0, text="甲"),
        Sentence(id=1, start=3.0, end=4.0, text="乙"),
    ]
    out = clip_srt(sentences, [[0.0, 1.0], [3.0, 4.0]], start=0.0, end=4.0)
    # 甲's 0-2s is clipped to the kept 0-1s; 乙 starts at 1.0s on the output timeline.
    assert "00:00:00,000 --> 00:00:01,000\n甲" in out
    assert "00:00:01,000 --> 00:00:02,000\n乙" in out


def test_span_entries_clip_to_part_and_rebase_time() -> None:
    sentences = [
        Sentence(id=0, start=1.0, end=2.0, text="前"),
        Sentence(id=1, start=3.0, end=6.0, text="跨"),
        Sentence(id=2, start=6.5, end=8.0, text="后"),
    ]
    entries = span_entries(sentences, begin=3.5, end=7.0)
    assert entries == [(0.0, 2.5, "跨"), (3.0, 3.5, "后")]


def test_ass_document_scales_to_output_frame() -> None:
    doc = ass_document([(0.0, 1.5, "测试")], 1080, 1920, style="clean")
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc
    style = next(line for line in doc.splitlines() if line.startswith("Style: Default,"))
    fields = style.split(",")
    # Fontsize is 5.2% of height, margin 14% of height, so text no longer
    # depends on libass's 384x288 SRT fallback canvas.
    assert int(fields[2]) == 100
    assert int(fields[21]) == 269
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,测试" in doc


def test_ass_document_escapes_override_braces() -> None:
    doc = ass_document([(0.0, 1.0, "{\\an8}粗体")], 1080, 1080)
    assert "{\\an8}" not in doc
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,(＼an8)粗体" in doc
