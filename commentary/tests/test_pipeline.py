"""Pipeline pieces with a scripted stand-in for the model (tests only -- the
demo path always uses the real provider)."""

from pathlib import Path

import pytest

from conftest import jpeg
from guide import bundle, clips, config, pipeline
from guide.llm import LLMError, parse_json_object


class ScriptedLLM:
    """Returns queued answers in order and records what it was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def json(self, **kw):
        self.calls.append(kw)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


TRANSCRIPT = {
    "segments": [
        {"start": 0.0, "end": 3.0, "text": "First clip the relay onto the rail."},
        {"start": 3.0, "end": 7.0, "text": "Hook the top edge first, then press down until it clicks."},
        {"start": 7.0, "end": 11.0, "text": "Now the red wire goes to terminal five."},
    ]
}


def make_bundle(tmp_path, n=24, fps=2.0, audio=False):
    frames = [(i / fps, jpeg(sharp=(i % 4 == 3))) for i in range(n)]
    z = tmp_path / "b.zip"
    bundle.write(z, "Test task", frames, ("audio.wav", b"RIFF....") if audio else None, started_at=0)
    return bundle.extract(z, tmp_path / "x")


# ---------------------------------------------------------------- bundle


def test_bundle_roundtrip(tmp_path):
    b = make_bundle(tmp_path, audio=True)
    assert len(b.frames) == 24 and b.frames[1]["t"] == 0.5
    assert b.audio and b.audio.name == "audio.wav"
    assert Path(b.frames[0]["path"]).exists()


def test_bundle_accepts_ios_top_level_folder(tmp_path):
    """The phone zips a directory, so files sit under capture-<id>/ in the zip."""
    import zipfile

    flat = tmp_path / "flat.zip"
    bundle.write(flat, "T", [(0.0, jpeg()), (0.5, jpeg())], ("audio.m4a", b"aac"), started_at=0)
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(flat) as src, zipfile.ZipFile(nested, "w") as dst:
        for name in src.namelist():
            dst.writestr(f"capture-cap_123/{name}", src.read(name))
    b = bundle.extract(nested, tmp_path / "out")
    assert len(b.frames) == 2 and Path(b.frames[1]["path"]).exists()
    assert b.audio and b.audio.name == "audio.m4a"


def test_bundle_rejects_zip_slip(tmp_path):
    import zipfile

    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.txt", "x")
        zf.writestr("manifest.json", '{"frames": []}')
    with pytest.raises(ValueError):
        bundle.extract(z, tmp_path / "out")


# ---------------------------------------------------------------- helpers


def test_verbatim_tips_drop_paraphrase():
    tips = ["Hook the top edge first, then press down", "Always hook the top first", "ok"]
    assert pipeline.verbatim_tips(tips, TRANSCRIPT) == ["Hook the top edge first, then press down"]


def test_normalize_spans_sorts_clamps_and_dedupes_overlap():
    spans = [{"t_start": 5, "t_end": 9, "summary": "b"}, {"t_start": -1, "t_end": 6, "summary": "a"}, {"t_start": 9, "t_end": 9.2, "summary": "tiny"}, {"t_start": 8, "t_end": 50, "summary": "c"}]
    out = pipeline.normalize_spans(spans, duration=12)
    assert [s["summary"] for s in out] == ["a", "b", "c"]
    assert out[0]["t_start"] == 0 and out[1]["t_start"] == 6 and out[2]["t_end"] == 12
    assert all(a["t_end"] <= b["t_start"] for a, b in zip(out, out[1:]))


def test_clip_words():
    assert pipeline.clip_words("one two three four five six", 5) == "one two three four five"
    assert pipeline.clip_words("short", 5) == "short"


# ---------------------------------------------------------------- clip limits


@pytest.mark.parametrize("w,h", [(1920, 1080), (1080, 1920), (960, 672), (504, 504), (320, 240)])
def test_clip_size_limits(w, h):
    cw, ch = clips.clip_size(w, h)
    assert cw <= config.CLIP_MAX_SIDE and ch <= config.CLIP_MAX_SIDE
    assert cw * ch <= config.CLIP_MAX_PIXELS
    assert cw % 2 == 0 and ch % 2 == 0
    assert abs(cw / ch - w / h) < 0.05


def test_clip_window_is_last_five_seconds():
    assert clips.clip_window(10, 30) == (25, 30)
    assert clips.clip_window(10, 12) == (10, 12)


def test_render_clip_respects_limits(tmp_path):
    b = make_bundle(tmp_path, n=40)
    size = clips.clip_size(480, 360)
    picked = clips.frames_for_clip(b.frames, 0, 8, fps=config.CLIP_FPS)  # asks for 8 s
    out = clips.render_clip([f["path"] for f in picked], config.CLIP_FPS, size, str(tmp_path / "c.mp4"))
    info = clips.probe(out)
    assert (info["width"], info["height"]) == size
    assert info["duration"] <= config.CLIP_MAX_SECONDS + 0.2


# ---------------------------------------------------------------- build_procedure


def split_answer(n):
    return {"title": "Relay wiring", "steps": [{"t_start": i * 1.5, "t_end": i * 1.5 + 1.4, "summary": f"s{i}"} for i in range(n)]}


def write_answer(n, long_title=False):
    rows = []
    for i in range(n):
        rows.append(
            {
                "title": "a very long title that breaks limits" if long_title else f"Step {i}",
                "instruction": "Do the thing.",
                "tips": ["Hook the top edge first, then press down", "made up quote here"],
                "tools": [],
                "parts": [],
                "check": f"Red wire on terminal {i}.",
            }
        )
    return {"steps": rows}


def test_build_procedure_happy_path(tmp_path):
    b = make_bundle(tmp_path, n=24)
    llm = ScriptedLLM([split_answer(7), write_answer(7), {"questions": [{"question": "Why tug?", "t": 4, "step_index": 2}]}])
    proc, _ = pipeline.build_procedure(b, TRANSCRIPT, llm)
    assert len(proc.steps) == 7 and proc.title == "Relay wiring"
    assert proc.steps[0].tips == ["Hook the top edge first, then press down"]  # paraphrase dropped
    assert proc.followups[0].step_id == proc.steps[2].id
    # reference = sharpest frame near the step end (every 4th frame is sharp)
    for s in proc.steps:
        idx = int(Path(s.reference_image).stem)
        assert idx % 4 == 3 or abs(idx / 2 - s.t_end) <= 1.0
    assert llm.calls[0]["model"] == config.AUTHOR_MODEL


def test_build_procedure_retries_word_limits_then_clips(tmp_path):
    b = make_bundle(tmp_path)
    llm = ScriptedLLM([split_answer(6), write_answer(6, long_title=True), write_answer(6, long_title=True), {"questions": []}])
    proc, _ = pipeline.build_procedure(b, TRANSCRIPT, llm)
    assert len(llm.calls) == 4  # split, write, retry write, follow-ups
    assert "word limits" in llm.calls[2]["parts"][-1]
    assert all(len(s.title.split()) <= 5 for s in proc.steps)


def test_followup_failure_is_not_fatal(tmp_path):
    b = make_bundle(tmp_path)
    llm = ScriptedLLM([split_answer(6), write_answer(6), LLMError("timeout")])
    proc, _ = pipeline.build_procedure(b, TRANSCRIPT, llm)
    assert proc.followups == []


def test_run_pipeline_end_to_end_marks_capture(tmp_path, store):
    from guide.schemas import CaptureStatus

    zsrc = tmp_path / "src"
    zsrc.mkdir()
    frames = [(i / 2, jpeg(sharp=(i % 4 == 3))) for i in range(24)]
    cap = store.save_capture(CaptureStatus(state="processing"))
    bundle.write(zsrc / "b.zip", "T", frames, ("audio.wav", b"RIFF"), started_at=0)
    store.put_media(f"captures/{cap.id}/bundle.zip", (zsrc / "b.zip").read_bytes())
    llm = ScriptedLLM([split_answer(6), write_answer(6), {"questions": []}])
    heard = []
    proc = pipeline.run_pipeline(store, cap.id, transcribe=lambda rel: heard.append(rel) or TRANSCRIPT, llm=llm)
    assert heard == [f"captures/{cap.id}/audio.wav"]
    cap = store.get_capture(cap.id)
    assert cap.state == "done" and cap.procedure_id == proc.id and proc.status == "draft"
    s0 = proc.steps[0]
    assert store.read_media(s0.reference_image) and s0.clip and len(s0.keyframes) >= 1


def test_run_pipeline_error_is_reported(tmp_path, store):
    from guide.schemas import CaptureStatus

    cap = store.save_capture(CaptureStatus(state="processing"))
    frames = [(i / 2, jpeg()) for i in range(10)]
    bundle.write(tmp_path / "b.zip", "T", frames, None, started_at=0)
    store.put_media(f"captures/{cap.id}/bundle.zip", (tmp_path / "b.zip").read_bytes())
    assert pipeline.run_pipeline(store, cap.id, transcribe=lambda r: {}, llm=ScriptedLLM([LLMError("no key")])) is None
    cap = store.get_capture(cap.id)
    assert cap.state == "error" and "no key" in cap.error


# ---------------------------------------------------------------- JSON parsing


def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 1} hope that helps') == {"a": 1}
    for bad in ["", "nope", "[1,2]", '{"a": }']:
        with pytest.raises(LLMError):
            parse_json_object(bad)


def test_run_pipeline_unexpected_crash_is_reported(tmp_path, store):
    """A crash in a dependency (e.g. an import error in the GPU worker) must not
    leave the capture stuck in 'processing'."""
    from guide.schemas import CaptureStatus

    cap = store.save_capture(CaptureStatus(state="processing"))
    bundle.write(tmp_path / "b.zip", "T", [(0.0, jpeg())], ("audio.wav", b"RIFF"), started_at=0)
    store.put_media(f"captures/{cap.id}/bundle.zip", (tmp_path / "b.zip").read_bytes())

    def broken_transcribe(rel):
        raise ModuleNotFoundError("No module named 'requests'")

    assert pipeline.run_pipeline(store, cap.id, transcribe=broken_transcribe, llm=ScriptedLLM([])) is None
    assert store.get_capture(cap.id).state == "error"
