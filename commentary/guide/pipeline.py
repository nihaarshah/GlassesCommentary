"""Capture -> draft procedure.

  1. unpack the bundle, score every frame's sharpness
  2. transcribe the narration with word timestamps (Whisper on GPU)
  3. split into 6-15 steps from transcript + sampled frames (hosted model)
  4. per step: sharpest frame near the end = reference; write title,
     instruction, verbatim tips, tools/parts and a concrete visual check
  5. render a <= 5 s clip and a few keyframes per step
  6. ask 2-3 follow-up questions about moments the narration didn't explain

Task-agnostic: nothing here knows what is being built.
"""

from __future__ import annotations

import json
import re
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from . import bundle as bundle_mod
from . import clips, config, frames
from .llm import LLM, ImagePart, LLMError, make_llm
from .schemas import INSTRUCTION_MAX_WORDS, TITLE_MAX_WORDS, FollowUp, Procedure, Step
from .store import Store

# --------------------------------------------------------------------------- transcript helpers


def transcript_lines(transcript: dict, t0: float | None = None, t1: float | None = None) -> str:
    """'[12.3-15.8] text' per segment, optionally limited to segments overlapping [t0, t1]."""
    out = []
    for s in transcript.get("segments", []):
        if t0 is not None and s["end"] < t0:
            continue
        if t1 is not None and s["start"] > t1:
            continue
        out.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    return "\n".join(out) or "(no narration)"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", text.lower())).strip()


def verbatim_tips(tips: list[str], transcript: dict) -> list[str]:
    """Keep only tips that really are the expert's words (normalized substring of
    the transcript). Paraphrases are dropped rather than passed off as quotes."""
    full = _norm(" ".join(s["text"] for s in transcript.get("segments", [])))
    kept = []
    for tip in tips:
        n = _norm(tip)
        if n and len(n.split()) >= 3 and n in full and tip.strip() not in kept:
            kept.append(tip.strip())
    return kept


def clip_words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit]).rstrip(",;:") if len(words) > limit else text.strip()


def normalize_spans(spans: list[dict], duration: float) -> list[dict]:
    """Sort, clamp to [0, duration], make contiguous-or-gapped but never overlapping,
    and drop empty spans."""
    spans = sorted(spans, key=lambda s: float(s["t_start"]))
    out = []
    prev_end = 0.0
    for s in spans:
        t0 = max(prev_end, min(duration, float(s["t_start"])))
        t1 = max(t0, min(duration, float(s["t_end"])))
        if t1 - t0 < 0.5:
            continue
        out.append({**s, "t_start": round(t0, 2), "t_end": round(t1, 2)})
        prev_end = t1
    return out


# --------------------------------------------------------------------------- model prompts

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t_start": {"type": "number"},
                    "t_end": {"type": "number"},
                    "summary": {"type": "string"},
                },
                "required": ["t_start", "t_end", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "steps"],
    "additionalProperties": False,
}

SPLIT_SYSTEM = f"""An expert recorded themselves doing a hands-on task once, narrating as
they went, from a head-mounted camera. Split the recording into {config.MIN_STEPS}-{config.MAX_STEPS}
steps a novice could follow one at a time.

A step is one physical change that is visible when done (a part mounted, a wire
landed, a screw tightened). Use the narration timestamps and the timestamped
frames. Steps are in order and don't overlap. t_end is when that step's result
is first fully visible. Skip preamble and chatter that isn't a step.
title: a short name for the whole procedure (<= 6 words)."""

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "instruction": {"type": "string"},
                    "tips": {"type": "array", "items": {"type": "string"}},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "parts": {"type": "array", "items": {"type": "string"}},
                    "check": {"type": "string"},
                },
                "required": ["title", "instruction", "tips", "tools", "parts", "check"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}

WRITE_SYSTEM = f"""You turn an expert's recorded steps into a guide shown on smart glasses.
For each step you get its narration and the expert's photo of the finished step.
Return one entry per step, same order.

- title: <= {TITLE_MAX_WORDS} words.
- instruction: <= {INSTRUCTION_MAX_WORDS} words, imperative, what to do.
- tips: exact quotes from that step's narration that help a novice (why, how,
  what to avoid). Copy the expert's words verbatim; no paraphrase. [] if none.
- tools, parts: short names, only those shown or mentioned.
- check: what must be visibly true in a photo when the step is done, in concrete
  visual terms: part names, wire colors, terminal or position labels exactly as
  printed. One or two sentences. This is used to verify a novice's work, so name
  every connection or part the step adds."""

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "t": {"type": "number"},
                    "step_index": {"type": "integer"},
                },
                "required": ["question", "t", "step_index"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

FOLLOWUP_SYSTEM = """You review an expert's narrated recording of a hands-on task. Find 2-3
moments where the expert did something deliberate that the narration didn't
explain (a habit, a check, a choice between options), and ask the expert about it
in one short, specific question each, e.g. "Why did you tug every wire?".
t = when it happens (seconds); step_index = 0-based step it belongs to."""


# --------------------------------------------------------------------------- pipeline


def _progress(store: Store, cap_id: str, stage: str, progress: float) -> None:
    cap = store.get_capture(cap_id)
    if cap:
        cap.stage, cap.progress = stage, round(progress, 2)
        store.save_capture(cap)


def _jpeg(frame: dict, max_side: int = 768) -> bytes:
    return frames.display_jpeg(Path(frame["path"]).read_bytes(), max_side=max_side)


def build_procedure(
    b: bundle_mod.Bundle,
    transcript: dict,
    llm: LLM,
    progress: Callable[[str, float], None] = lambda s, p: None,
) -> tuple[Procedure, dict]:
    """Model calls only; returns the draft procedure (media paths are local) plus
    the per-step frame choices for media rendering."""
    for f in b.frames:
        f.setdefault("sharpness", frames.sharpness_jpeg(Path(f["path"]).read_bytes()))
    # Transcript times are relative to the audio; shift onto the frame clock.
    if b.audio_offset_s:
        for s in transcript.get("segments", []):
            s["start"] += b.audio_offset_s
            s["end"] += b.audio_offset_s
    duration = b.frames[-1]["t"]

    progress("splitting into steps", 0.35)
    sampled = frames.evenly_spaced(b.frames, config.SPLIT_SAMPLE_FRAMES)
    parts: list = [f"Recording length: {duration:.1f} s.\nNARRATION:\n{transcript_lines(transcript)}\n\nFRAMES:"]
    parts += [ImagePart(_jpeg(f, 512), f"t={f['t']:.1f}s") for f in sampled]
    split = llm.json(system=SPLIT_SYSTEM, parts=parts, schema=SPLIT_SCHEMA, model=config.AUTHOR_MODEL, effort="high", max_tokens=8000, timeout_s=300)
    spans = normalize_spans(split["steps"], duration)
    if not (config.MIN_STEPS <= len(spans) <= config.MAX_STEPS):
        # Not fatal for review, but tell the reviewer via the title.
        print(f"[pipeline] model returned {len(spans)} steps (wanted {config.MIN_STEPS}-{config.MAX_STEPS})")
    if not spans:
        raise LLMError("model returned no usable steps")

    progress("writing steps", 0.55)
    refs = [frames.pick_reference(b.frames, s["t_end"]) for s in spans]
    parts = []
    for i, (s, ref) in enumerate(zip(spans, refs)):
        parts.append(f"STEP {i + 1} ({s['t_start']:.1f}-{s['t_end']:.1f}s): {s['summary']}\nNARRATION:\n{transcript_lines(transcript, s['t_start'], s['t_end'])}")
        parts.append(ImagePart(_jpeg(ref), f"STEP {i + 1} finished (expert photo):"))

    written = None
    errors = ""
    for attempt in range(2):
        out = llm.json(system=WRITE_SYSTEM, parts=parts + ([errors] if errors else []), schema=WRITE_SCHEMA, model=config.AUTHOR_MODEL, effort="high", max_tokens=16000, timeout_s=300)
        rows = out.get("steps", [])
        if len(rows) != len(spans):
            errors = f"Previous answer had {len(rows)} entries; return exactly {len(spans)}."
            continue
        bad = [i + 1 for i, r in enumerate(rows) if len(r["title"].split()) > TITLE_MAX_WORDS or len(r["instruction"].split()) > INSTRUCTION_MAX_WORDS]
        written = rows
        if not bad:
            break
        errors = f"Steps {bad} broke the word limits (title <= {TITLE_MAX_WORDS}, instruction <= {INSTRUCTION_MAX_WORDS}). Shorten them."
    if written is None:
        raise LLMError("model never returned one entry per step")

    steps = []
    for s, ref, w in zip(spans, refs, written):
        steps.append(
            Step(
                title=clip_words(w["title"], TITLE_MAX_WORDS),
                instruction=clip_words(w["instruction"], INSTRUCTION_MAX_WORDS),
                tips=verbatim_tips(w.get("tips", []), transcript),
                tools=w.get("tools", []),
                parts=w.get("parts", []),
                t_start=s["t_start"],
                t_end=s["t_end"],
                reference_image=ref["path"],
                check=w["check"],
            )
        )

    progress("follow-up questions", 0.75)
    followups: list[FollowUp] = []
    try:
        fparts: list = [f"NARRATION:\n{transcript_lines(transcript)}\n\nSTEPS:\n" + "\n".join(f"{i}: {st.title} ({st.t_start}-{st.t_end}s)" for i, st in enumerate(steps))]
        fparts += [ImagePart(_jpeg(f, 384), f"t={f['t']:.1f}s") for f in frames.evenly_spaced(b.frames, 12)]
        fq = llm.json(system=FOLLOWUP_SYSTEM, parts=fparts, schema=FOLLOWUP_SCHEMA, model=config.AUTHOR_MODEL, effort="medium", max_tokens=4000, timeout_s=180)
        for q in fq.get("questions", [])[:3]:
            idx = q.get("step_index")
            followups.append(FollowUp(question=q["question"], t=q.get("t"), step_id=steps[idx].id if isinstance(idx, int) and 0 <= idx < len(steps) else None))
    except LLMError as exc:  # follow-ups are optional
        print(f"[pipeline] follow-ups skipped: {exc}")

    proc = Procedure(title=clip_words(split.get("title") or b.title, 8) or b.title, steps=steps, followups=followups)
    return proc, {"spans": spans}


def render_media(store: Store, proc: Procedure, b: bundle_mod.Bundle, prefix: str) -> None:
    """Reference images, keyframes and clips -> Volume; rewrites the step paths."""
    import cv2

    first = cv2.imread(b.frames[0]["path"])
    size = clips.clip_size(first.shape[1], first.shape[0])
    for i, step in enumerate(proc.steps):
        ref_local = step.reference_image
        step.reference_image = store.put_media(f"{prefix}/{step.id}/ref.jpg", frames.display_jpeg(Path(ref_local).read_bytes()), commit=False)
        window = [f for f in b.frames if step.t_start <= f["t"] <= step.t_end] or [frames.pick_reference(b.frames, step.t_end)]
        kfs = frames.evenly_spaced(window, config.KEYFRAMES_PER_STEP)
        step.keyframes = [
            store.put_media(f"{prefix}/{step.id}/kf{k}.jpg", frames.display_jpeg(Path(f["path"]).read_bytes(), max_side=config.CLIP_MAX_SIDE), commit=False)
            for k, f in enumerate(kfs)
        ]
        t0, t1 = clips.clip_window(step.t_start, step.t_end)
        clip_frames = clips.frames_for_clip(b.frames, t0, t1, fps=config.CLIP_FPS)
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp, "clip.mp4"))
            try:
                clips.render_clip([f["path"] for f in clip_frames], config.CLIP_FPS, size, out)
                step.clip = store.put_media(f"{prefix}/{step.id}/clip.mp4", Path(out).read_bytes(), commit=False)
            except Exception as exc:  # noqa: BLE001 -- keyframes still cover Show me
                print(f"[pipeline] clip for step {i + 1} failed: {exc}")
                step.clip = None
    store.commit_media()


def run_pipeline(store: Store, cap_id: str, transcribe: Callable[[str], dict], llm: LLM | None = None) -> Procedure | None:
    cap = store.get_capture(cap_id)
    if not cap:
        raise ValueError(f"unknown capture {cap_id}")
    prog = lambda stage, p: _progress(store, cap_id, stage, p)  # noqa: E731
    try:
        llm = llm or make_llm()
        with tempfile.TemporaryDirectory() as tmp:
            prog("unpacking", 0.05)
            zip_path = store.media_path(f"captures/{cap_id}/bundle.zip")
            b = bundle_mod.extract(zip_path, Path(tmp) / "bundle")
            if b.audio:
                prog("transcribing", 0.15)
                rel = f"captures/{cap_id}/{b.audio.name}"
                store.put_media(rel, b.audio.read_bytes())
                transcript = transcribe(rel)
            else:
                transcript = {"segments": []}
            store.put_media(f"captures/{cap_id}/transcript.json", json.dumps(transcript).encode())
            proc, _ = build_procedure(b, transcript, llm, prog)
            proc.source_capture = cap_id
            prog("rendering clips", 0.85)
            render_media(store, proc, b, prefix=f"procedures/{proc.id}")
        store.save_procedure(proc)
        cap = store.get_capture(cap_id)
        cap.state, cap.stage, cap.progress, cap.procedure_id = "done", "ready for review", 1.0, proc.id
        store.save_capture(cap)
        return proc
    except Exception as exc:  # noqa: BLE001 -- any failure must surface as state=error, never hang
        traceback.print_exc()
        cap = store.get_capture(cap_id)
        cap.state, cap.error = "error", f"{type(exc).__name__}: {exc}"[:300]
        store.save_capture(cap)
        return None
