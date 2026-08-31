"""Stream frames to the Modal commentary endpoint and print live narration.

Self-contained laptop/webcam test client -- useful for quick debugging of the
Modal backend without needing the iOS app or real glasses. The real capture
path is the iOS app in this repo (GlassesCommentary/GlassesCommentaryStream.swift),
which streams real glasses frames to the same endpoint.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from typing import Iterator

import cv2
import numpy as np
import requests


def iter_frames(source: str | int) -> Iterator[tuple[int, np.ndarray]]:
    if isinstance(source, str) and source.isdigit():
        source = int(source)
    if isinstance(source, int) and sys.platform == "darwin":
        cap = cv2.VideoCapture(source, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield idx, frame
            idx += 1
    finally:
        cap.release()


def encode_jpeg_b64(image_bgr: np.ndarray, quality: int = 85) -> str:
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Failed to JPEG-encode frame")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stream frames to Modal for live commentary")
    p.add_argument("--source", default="0", help="Webcam index or path to video (glasses feed later)")
    p.add_argument("--endpoint", default=os.getenv("COMMENTARY_URL", ""))
    p.add_argument("--every-s", type=float, default=4.0, help="Seconds between commentary requests")
    p.add_argument("--prompt", default="Describe what you see in one short sentence.")
    p.add_argument("--max-width", type=int, default=640)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.endpoint:
        raise SystemExit("Set --endpoint or COMMENTARY_URL to the deployed Modal endpoint")

    last_t = 0.0
    for idx, frame in iter_frames(args.source):
        now = time.time()
        if now - last_t < args.every_s:
            continue
        last_t = now

        h, w = frame.shape[:2]
        if w > args.max_width:
            scale = args.max_width / float(w)
            frame = cv2.resize(frame, (args.max_width, int(h * scale)))

        try:
            resp = requests.post(
                args.endpoint,
                json={"image_b64": encode_jpeg_b64(frame), "prompt": args.prompt},
                timeout=60,
            )
            resp.raise_for_status()
            commentary = resp.json().get("commentary", "")
        except Exception as exc:  # noqa: BLE001 -- keep the loop alive on a bad/slow request
            print(f"[commentary] request failed: {exc}")
            continue

        print(f"[frame {idx}] {commentary}")


if __name__ == "__main__":
    main()
