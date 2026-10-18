"""Local backend for exercising the glasses web app screens in a browser.

TEST ONLY -- never deployed. It serves the real API routes over an in-memory
store with the seed procedure, and a scripted verifier (fail, then pass, then
unsure, ...) so every Result screen can be seen without model calls. Point the
web app at it with ?api=http://localhost:8787.

  python commentary/tests/ui_backend.py
"""

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile  # noqa: E402

import uvicorn  # noqa: E402

from guide.api import Services, create_app  # noqa: E402
from guide.seed_loader import load_seed  # noqa: E402
from guide.store import Store  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "guide" / "seed"
SCRIPT = itertools.cycle(
    [
        {"verdict": "fail", "reason": "Seal-in wire 14 to A1 missing"},
        {"verdict": "pass", "reason": "All three wires match"},
        {"verdict": "unsure", "reason": "Move closer to terminals 13 and 14"},
        {"verdict": "hold_still", "reason": "Too blurry. Hold still and check again."},
    ]
)


def main() -> None:
    store = Store({}, Path(tempfile.mkdtemp()) / "media")
    files = {p.relative_to(SEED).as_posix(): p.read_bytes() for p in SEED.rglob("*.jpg")}
    proc = load_seed(store, (SEED / "din_rail_start_stop.json").read_text(), files)

    # Give the UI something for Show me: keyframes on every step (diagram before ->
    # after), and a real H.264 clip on step 2 to exercise <video>.
    from guide import clips

    for i, step in enumerate(proc.steps):
        step.keyframes = [proc.steps[max(i - 1, 0)].reference_image, step.reference_image]
    with tempfile.TemporaryDirectory() as tmp:
        paths = [str(SEED / "din_rail_start_stop" / f"step{1 + (k // 8)}.jpg") for k in range(16)]
        out = clips.render_clip(paths, 8, clips.clip_size(600, 420), str(Path(tmp, "c.mp4")))
        proc.steps[1].clip = store.put_media("ui/clip.mp4", Path(out).read_bytes())
    store.save_procedure(proc)

    app = create_app(store, Services(verify=lambda ref, frame, check: next(SCRIPT)))

    # The phone isn't streaming here, so feed the run one frame per check.
    @app.middleware("http")
    async def feed_frame(request, call_next):
        if request.url.path.endswith("/check"):
            run_id = request.url.path.split("/")[2]
            ref = next(iter(files.values()))
            store.push_frame(run_id, {"t": __import__("time").time(), "sharpness": 999.0, "jpeg": ref}, keep=6)
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
