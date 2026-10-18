"""Frame utilities: sharpness scoring, picking the best frame, display-sized JPEGs."""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from . import config


def decode_jpeg(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def encode_jpeg(img: np.ndarray, quality: int = 82) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


def resize_max_side(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max_side / float(max(h, w))
    if scale >= 1.0:
        return img
    return cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def sharpness(img: np.ndarray) -> float:
    """Variance of the Laplacian on a 600 px-wide grayscale copy, so the score
    doesn't depend on the stream resolution."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape[:2]
    if w != 600:
        gray = cv2.resize(gray, (600, max(1, round(h * 600 / w))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpness_jpeg(data: bytes) -> float:
    img = decode_jpeg(data)
    return sharpness(img) if img is not None else 0.0


def display_jpeg(data: bytes, max_side: int = config.DISPLAY_IMAGE_MAX_PX, quality: int = 75) -> bytes:
    img = decode_jpeg(data)
    if img is None:
        raise ValueError("not a JPEG")
    return encode_jpeg(resize_max_side(img, max_side), quality)


def pick_sharpest(frames: Sequence[dict], now: float | None = None, max_age_s: float | None = None) -> dict | None:
    """frames: [{"t": float, "sharpness": float, ...}]. Returns the sharpest frame no
    older than max_age_s (relative to `now`, default: the newest frame)."""
    if not frames:
        return None
    ref = now if now is not None else max(f["t"] for f in frames)
    fresh = [f for f in frames if max_age_s is None or ref - f["t"] <= max_age_s]
    if not fresh:
        return None
    return max(fresh, key=lambda f: (f["sharpness"], f["t"]))


def pick_reference(frames: Sequence[dict], t_end: float, window_s: float = 4.0) -> dict | None:
    """Sharpest frame near the end of a step: from t_end - window_s up to t_end + 1 s
    (a little slack because narration often runs slightly ahead of the hands).
    Ties go to the later frame. Falls back to the frame closest to t_end."""
    if not frames:
        return None
    near = [f for f in frames if t_end - window_s <= f["t"] <= t_end + 1.0]
    if near:
        return max(near, key=lambda f: (f["sharpness"], f["t"]))
    return min(frames, key=lambda f: abs(f["t"] - t_end))


def evenly_spaced(frames: Sequence[dict], n: int, t0: float | None = None, t1: float | None = None) -> list[dict]:
    pool = [f for f in frames if (t0 is None or f["t"] >= t0) and (t1 is None or f["t"] <= t1)]
    if len(pool) <= n:
        return list(pool)
    idx = np.linspace(0, len(pool) - 1, n).round().astype(int)
    return [pool[i] for i in idx]
