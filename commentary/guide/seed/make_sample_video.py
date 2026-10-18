"""Make a synthetic narrated "expert recording" of the demo task to exercise the
capture pipeline end to end before a real recording exists.

Frames are the seed wiring diagrams under a fake head-camera (perspective wobble,
motion blur while "working", sharp once a step is done); narration is macOS `say`.
It tests the pipeline's mechanics, not real-world accuracy.

  python commentary/guide/seed/make_sample_video.py /tmp/sample.mp4     (macOS only)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2]))
from guide.ffmpeg import exe as ffmpeg_exe  # noqa: E402
from render_diagrams import render  # noqa: E402

FPS = 10
# (diagram state shown, narration). State 0 = empty rail before parts go on.
SCRIPT = [
    (0, "Okay, today I'm wiring a twenty four volt start stop circuit with a holding contact."),
    (1, "First I clip the contactor, the stop button, the start button and the pilot light onto the rail. Hook the top edge first, then press down until it clicks."),
    (2, "Red wire from the plus twenty four volt terminal to stop terminal eleven. Strip about eight millimetres, no more."),
    (3, "Next, stop terminal twelve over to start terminal thirteen, again in red."),
    (4, "Start terminal fourteen goes to the coil, terminal A one on the contactor."),
    (5, "Blue wire from A two on the contactor back to zero volts."),
    (6, "Pilot light across the coil. X one to A one in red, X two to A two in blue. Two wires can share a terminal if both are fully under the clamp."),
    (7, "Now the seal in. Contactor thirteen to start thirteen, and contactor fourteen to A one. This keeps the coil on after you let go of start."),
    (8, "Last thing, I go round and give every wire a little tug."),
]


def say(text: str, out: Path) -> float:
    """macOS TTS straight to 16-bit WAV; returns its duration."""
    import wave

    subprocess.run(["say", "-r", "185", "--data-format=LEI16@22050", "-o", str(out), text], check=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def bgr(state: int) -> np.ndarray:
    if state == 0:
        return np.full((420, 600, 3), 235, np.uint8)  # empty bench
    return np.array(render(state).convert("RGB"))[:, :, ::-1].copy()


def camera(img: np.ndarray, t: float, working: bool, rng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    amp = 18 if working else 4
    jitter = rng.normal(0, amp, (4, 2)).astype(np.float32)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + jitter + np.float32([[20, 10], [-20, 15], [-10, -10], [15, -5]])
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(img, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    if working:
        k = int(rng.integers(7, 15))
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1.0 / k
        out = cv2.filter2D(out, -1, kernel)
    noise = rng.normal(0, 3, out.shape).astype(np.int16)
    out = np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return cv2.resize(out, (960, 672))


def main(out_path: str) -> None:
    rng = np.random.default_rng(7)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio_parts, frame_i = [], 0
        for seg, (state, text) in enumerate(SCRIPT):
            aiff = tmp / f"seg{seg}.wav"
            dur = say(text, aiff) + 0.6
            audio_parts.append((aiff, dur))
            prev, done = bgr(max(state - 1, 0)), bgr(state)
            n = int(round(dur * FPS))
            for i in range(n):
                frac = i / max(1, n - 1)
                working = 0.15 < frac < 0.7 and state > 0
                base = prev if frac < 0.45 else done
                cv2.imwrite(str(tmp / f"f{frame_i:05d}.jpg"), camera(base, frame_i / FPS, working, rng))
                frame_i += 1
        # Pad each narration segment to its frame span so audio and frames line up.
        parts = []
        for aiff, dur in audio_parts:
            padded = tmp / f"{aiff.stem}_pad.wav"
            subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(aiff), "-af", "apad", "-t", f"{dur:.3f}", "-ar", "22050", "-ac", "1", str(padded)], check=True)  # fmt: skip
            parts.append(padded)
        with open(tmp / "list2.txt", "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list2.txt"), str(tmp / "narr.wav")], check=True)  # fmt: skip
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%05d.jpg"),
             "-i", str(tmp / "narr.wav"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out_path],
            check=True,
        )  # fmt: skip
    print(f"wrote {out_path} ({frame_i / FPS:.1f} s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample.mp4")
