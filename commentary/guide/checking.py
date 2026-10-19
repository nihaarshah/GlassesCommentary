"""One Check press: pick the image, verify it, record the attempt."""

from __future__ import annotations

import time
from typing import Callable

from . import config, frames
from .schemas import CheckAttempt, Run, new_id, now
from .store import Store


def _wait_for_photo(store: Store, run_id: str, request_id: str, wait_s: float) -> bytes | None:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        photo = store.kv.get(f"photo:{run_id}")
        if photo and photo.get("request") == request_id:
            return photo["jpeg"]
        time.sleep(0.25)
    return None


def _fresh_frames(store: Store, run_id: str, pressed_at: float, wait_s: float, min_frames: int) -> list[dict]:
    """Frames uploaded after the press, waiting up to wait_s for min_frames of them."""
    deadline = time.time() + wait_s
    while True:
        fresh = [f for f in store.recent_frames(run_id) if f["t"] >= pressed_at]
        if len(fresh) >= min_frames or time.time() >= deadline:
            return fresh
        time.sleep(0.2)


def pick_image(store: Store, run: Run, pressed_at: float | None = None) -> tuple[bytes | None, str]:
    """Still photo if configured (and the phone answers in time), else the sharpest
    stream frame taken after the press, else the sharpest recent one."""
    if config.CHECK_SOURCE == "photo":
        req = new_id("photo")
        store.request_photo(run.id, req)
        jpeg = _wait_for_photo(store, run.id, req, config.PHOTO_WAIT_S)
        if jpeg:
            return jpeg, "photo"
    pressed_at = pressed_at if pressed_at is not None else now()
    fresh = _fresh_frames(store, run.id, pressed_at, config.CHECK_FRESH_WAIT_S, config.CHECK_FRESH_MIN_FRAMES)
    best = frames.pick_sharpest(fresh) if fresh else None
    if best is None:
        best = frames.pick_sharpest(store.recent_frames(run.id), now=now(), max_age_s=config.FRAME_MAX_AGE_S)
    return (best["jpeg"], "stream") if best else (None, "stream")


def run_check(store: Store, verify: Callable[[bytes | None, bytes, str], dict], run: Run) -> Run:
    started = time.time()
    proc = store.get_procedure(run.procedure_id)
    step = proc.steps[run.current_step]
    jpeg, source = pick_image(store, run, pressed_at=now())

    if jpeg is None:
        result = {"verdict": "error", "reason": "No camera frames. Is the phone streaming?"}
        image_rel = None
    else:
        ref = store.read_media(step.reference_image) if step.reference_image else None
        try:
            check_text = step.check
            if proc.check_context:
                check_text = f"{step.check}\n(Setup note: {proc.check_context})"
            result = verify(ref, jpeg, check_text)
        except Exception:  # noqa: BLE001 -- a crash is an error verdict, never a pass
            result = {"verdict": "error", "reason": "Check failed. Try again."}
        n = len(run.steps[run.current_step].attempts) + 1
        image_rel = store.put_media(
            f"runs/{run.id}/{step.id}_{n}.jpg", frames.display_jpeg(jpeg, max_side=1024, quality=80)
        )

    run.steps[run.current_step].attempts.append(
        CheckAttempt(
            image=image_rel,
            verdict=result["verdict"],
            reason=result["reason"],
            source=source,
            latency_ms=int((time.time() - started) * 1000),
        )
    )
    run.screen = "result"
    return run
