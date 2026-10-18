"""Step verifier: expert reference image + current frame + check text -> verdict.

Rules (from the spec):
- Strict JSON {verdict, reason <= 12 words}.
- A blurry frame gets "hold_still" without calling the model.
- "unsure" tells the user what to look at more closely.
- Errors and timeouts never auto-pass: they come back as verdict "error".
"""

from __future__ import annotations

from typing import Callable

from . import config, frames
from .llm import LLM, ImagePart, LLMError, make_llm
from .schemas import REASON_MAX_WORDS

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "unsure"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

SYSTEM = f"""You check one step of a hands-on procedure from a head-mounted camera photo.

You get: the step's CHECK (what must be visibly true when the step is done), the
expert's REFERENCE photo of the finished step (it may be a diagram), and the
worker's CURRENT photo.

Judge only the CHECK, using the CURRENT photo. The reference shows what good looks
like; framing, lighting and angle will differ. Look for the specific parts, wire
colors and terminal labels the CHECK names.

- "pass": everything the CHECK requires is clearly visible and correct.
- "fail": something the CHECK requires is visibly missing or wrong. Name it.
- "unsure": you cannot see enough to decide (cut off, too far, occluded, glare).
  Say exactly what to bring closer or into view.

Never pass on a guess. If any required item can't be confirmed, answer "unsure".
reason: at most {REASON_MAX_WORDS} words, plain words for the worker, e.g.
"Seal-in wire 14 to A1 missing" or "Move closer to contactor terminals 13-14"."""


def clip_reason(reason: str, limit: int = REASON_MAX_WORDS) -> str:
    words = (reason or "").split()
    return " ".join(words[:limit]) if words else ""


def parse_verdict(data: dict) -> dict:
    """Normalize model JSON to {"verdict", "reason"}. Anything malformed becomes
    an error verdict -- never a pass."""
    verdict = str(data.get("verdict", "")).strip().lower() if isinstance(data, dict) else ""
    reason = clip_reason(str(data.get("reason", "")).strip()) if isinstance(data, dict) else ""
    if verdict not in ("pass", "fail", "unsure"):
        return {"verdict": "error", "reason": "Couldn't read the check result. Try again."}
    if not reason:
        reason = {"pass": "Looks right.", "fail": "Doesn't match the check.", "unsure": "Move closer and hold still."}[verdict]
    return {"verdict": verdict, "reason": reason}


def verify(
    llm: LLM,
    reference_jpeg: bytes | None,
    frame_jpeg: bytes,
    check_text: str,
    *,
    model: str = config.VERIFY_MODEL,
    timeout_s: float = config.VERIFY_TIMEOUT_S,
    blur_threshold: float = config.BLUR_THRESHOLD,
) -> dict:
    img = frames.decode_jpeg(frame_jpeg)
    if img is None:
        return {"verdict": "error", "reason": "No usable photo. Try again."}
    if frames.sharpness(img) < blur_threshold:
        return {"verdict": "hold_still", "reason": "Too blurry. Hold still and check again."}

    parts: list = [f"CHECK: {check_text}"]
    if reference_jpeg:
        parts.append(ImagePart(frames.display_jpeg(reference_jpeg), "REFERENCE (expert, finished step):"))
    parts.append(ImagePart(frames.display_jpeg(frame_jpeg, max_side=1280, quality=85), "CURRENT (worker):"))
    try:
        data = llm.json(
            system=SYSTEM,
            parts=parts,
            schema=VERDICT_SCHEMA,
            model=model,
            effort="low",
            max_tokens=2000,
            timeout_s=timeout_s,
        )
    except LLMError as exc:
        # Detail goes to the logs; the wearer gets something short and actionable.
        print(f"[verifier] {exc}")
        reason = "Checker timed out. Re-check." if "timed out" in str(exc) else "Checker unavailable. Re-check or override."
        return {"verdict": "error", "reason": reason}
    return parse_verdict(data)


def make_verifier() -> Callable[[bytes | None, bytes, str], dict]:
    llm = make_llm()
    return lambda ref, frame, check: verify(llm, ref, frame, check)
