"""Short step clips for "Show me": <= 5 s, <= 400 px per side, <= 70,000 pixels."""

from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from . import config, ffmpeg


def clip_size(w: int, h: int, max_side: int = config.CLIP_MAX_SIDE, max_pixels: int = config.CLIP_MAX_PIXELS) -> tuple[int, int]:
    """Largest even-sized (w, h) with the same aspect ratio within both limits."""
    scale = min(1.0, max_side / max(w, h), math.sqrt(max_pixels / (w * h)))
    cw, ch = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    while cw * ch > max_pixels:  # rounding can't push it over, but be certain
        cw, ch = cw - 2, max(2, round(ch * (cw - 2) / cw) // 2 * 2)
    return max(2, cw), max(2, ch)


def clip_window(t_start: float, t_end: float, max_s: float = config.CLIP_MAX_SECONDS) -> tuple[float, float]:
    """The last max_s seconds of a step (the part that shows the result)."""
    t0 = max(t_start, t_end - max_s)
    return t0, t_end


def frames_for_clip(frames: Sequence[dict], t0: float, t1: float, fps: float) -> list[dict]:
    """Nearest source frame for each output tick in [t0, t1)."""
    if not frames:
        return []
    n = max(1, int(round((t1 - t0) * fps)))
    out = []
    for i in range(n):
        t = t0 + i / fps
        out.append(min(frames, key=lambda f: abs(f["t"] - t)))
    return out


def render_clip(frame_paths: Sequence[str], fps: float, size: tuple[int, int], out_path: str) -> str:
    """H.264 MP4, no audio, faststart so it can stream over the glasses link."""
    if not frame_paths:
        raise ValueError("no frames for clip")
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(frame_paths):
            Path(tmp, f"{i:04d}.jpg").symlink_to(Path(p).resolve())
        w, h = size
        cmd = [
            ffmpeg.exe(), "-y", "-loglevel", "error",
            "-framerate", str(fps), "-i", str(Path(tmp, "%04d.jpg")),
            "-vf", f"scale={w}:{h}:flags=area",
            "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-an", "-t", str(config.CLIP_MAX_SECONDS),
            out_path,
        ]  # fmt: skip
        subprocess.run(cmd, check=True)
    return out_path


def probe(path: str) -> dict:
    """Width, height, duration of a video (for tests and sanity checks)."""
    import cv2

    cap = cv2.VideoCapture(path)
    try:
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 1.0
    finally:
        cap.release()
    return {"width": w, "height": h, "duration": n / fps}
