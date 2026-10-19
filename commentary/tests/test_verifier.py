import pytest
from fastapi.testclient import TestClient

from conftest import jpeg
from guide import verifier
from guide.api import Services, create_app
from guide.llm import LLMError


class OneShot:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def json(self, **kw):
        self.calls.append(kw)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(
    "data,verdict",
    [
        ({"verdict": "pass", "reason": "All wires match."}, "pass"),
        ({"verdict": "FAIL", "reason": "Seal-in wire missing."}, "fail"),
        ({"verdict": "unsure", "reason": ""}, "unsure"),
        ({"verdict": "maybe", "reason": "x"}, "error"),
        ({"reason": "no verdict"}, "error"),
        ("not a dict", "error"),
    ],
)
def test_parse_verdict(data, verdict):
    out = verifier.parse_verdict(data)
    assert out["verdict"] == verdict and out["reason"]


def test_reason_clipped_to_12_words():
    out = verifier.parse_verdict({"verdict": "fail", "reason": " ".join(["word"] * 30)})
    assert len(out["reason"].split()) == 12


def test_blurry_frame_holds_still_without_model():
    llm = OneShot({"verdict": "pass", "reason": "x"})
    out = verifier.verify(llm, None, jpeg(sharp=False), "Red wire on 5")
    assert out["verdict"] == "hold_still" and llm.calls == []


def test_model_errors_never_pass():
    for exc in (LLMError("model timed out"), LLMError("model declined")):
        out = verifier.verify(OneShot(exc), jpeg(), jpeg(), "Red wire on 5")
        assert out["verdict"] == "error"
        assert "{" not in out["reason"] and len(out["reason"].split()) <= 12


def test_verify_sends_check_reference_and_frame():
    llm = OneShot({"verdict": "fail", "reason": "Wire 14 to A1 missing"})
    out = verifier.verify(llm, jpeg(), jpeg(), "Red wire from 14 to A1")
    assert out == {"verdict": "fail", "reason": "Wire 14 to A1 missing"}
    call = llm.calls[0]
    assert call["parts"][0] == "CHECK: Red wire from 14 to A1"
    assert sum(1 for p in call["parts"] if not isinstance(p, str)) == 2
    assert call["schema"]["properties"]["verdict"]["enum"] == ["pass", "fail", "unsure"]


def test_check_endpoint_records_attempts(seeded):
    store, proc = seeded
    answers = iter([{"verdict": "fail", "reason": "Seal-in wire missing"}, {"verdict": "pass", "reason": "Looks right"}])

    def verify(ref, frame, check):
        assert ref and frame and check.startswith(proc.steps[0].check)
        return next(answers)

    c = TestClient(create_app(store, Services(verify=verify)))
    rid = c.post("/runs", json={"procedure_id": proc.id}).json()["id"]
    assert c.post(f"/runs/{rid}/check").json()["last_attempt"]["verdict"] == "error"  # no frames yet
    c.post(f"/runs/{rid}/frames", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    r = c.post(f"/runs/{rid}/check").json()
    assert r["screen"] == "result" and r["last_attempt"]["verdict"] == "fail"
    assert r["issues_caught"] == 1
    assert store.read_media(r["last_attempt"]["image"])
    r = c.post(f"/runs/{rid}/check").json()
    assert r["last_attempt"]["verdict"] == "pass" and r["last_attempt"]["latency_ms"] >= 0
    assert len(r["steps"][0]["attempts"]) == 3


def test_verifier_crash_is_error_not_pass(seeded):
    store, proc = seeded

    def boom(ref, frame, check):
        raise RuntimeError("kaboom")

    c = TestClient(create_app(store, Services(verify=boom)))
    rid = c.post("/runs", json={"procedure_id": proc.id}).json()["id"]
    c.post(f"/runs/{rid}/frames", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    assert c.post(f"/runs/{rid}/check").json()["last_attempt"]["verdict"] == "error"


def test_check_context_reaches_verifier(seeded):
    store, proc = seeded
    p = store.get_procedure(proc.id)
    p.check_context = "Panel shown as a diagram on a screen."
    store.save_procedure(p)
    seen = []
    c = TestClient(create_app(store, Services(verify=lambda r, f, check: seen.append(check) or {"verdict": "pass", "reason": "ok"})))
    rid = c.post("/runs", json={"procedure_id": proc.id}).json()["id"]
    c.post(f"/runs/{rid}/frames", content=jpeg(), headers={"Content-Type": "image/jpeg"})
    c.post(f"/runs/{rid}/check")
    assert seen[0].startswith(proc.steps[0].check) and "Setup note: Panel shown as a diagram" in seen[0]


def test_check_prefers_frames_taken_after_the_press(seeded):
    """Frames from before the press (e.g. looking away to click) must not be judged
    when fresh ones arrive."""
    import threading
    import time as _time

    from guide import checking, config, frames as fr
    from guide.schemas import Run

    store, proc = seeded
    run = Run.start(proc, "t")
    store.save_run(run)
    before = jpeg(sharp=True)  # very sharp, but taken before the press
    store.push_frame(run.id, {"t": _time.time() - 1, "sharpness": 9999.0, "jpeg": before}, keep=6)
    after = jpeg(sharp=True, size=(320, 240))
    pressed = _time.time()

    def upload_later():
        _time.sleep(0.3)
        store.push_frame(run.id, {"t": _time.time(), "sharpness": fr.sharpness_jpeg(after), "jpeg": after}, keep=6)

    threading.Thread(target=upload_later).start()
    old = config.CHECK_FRESH_WAIT_S
    config.CHECK_FRESH_WAIT_S, config.CHECK_FRESH_MIN_FRAMES = 2.0, 1
    try:
        got, _ = checking.pick_image(store, run, pressed_at=pressed)
    finally:
        config.CHECK_FRESH_WAIT_S, config.CHECK_FRESH_MIN_FRAMES = old, 2
    assert got == after
