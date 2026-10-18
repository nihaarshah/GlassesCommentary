"""Synthetic smoke set for the verifier: the seal-in step done right vs. with the
13-14 seal-in wiring left out (the planted mistake), under a fake head-camera.
It checks the eval plumbing and gives a first signal; it is NOT a substitute for
real photos of the panel, which the >= 9/10 targets are about.

  cd commentary && python -m eval.make_smoke_set eval/data/smoke
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guide" / "seed"))
from make_sample_video import camera  # noqa: E402
from render_diagrams import render  # noqa: E402

SEAL_IN_STEP = 7
N_EACH = 5


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    rows = []
    for i in range(N_EACH):
        for kind, omit, label, tag in (("ok", frozenset(), "pass", ""), ("missing_seal_in", frozenset({SEAL_IN_STEP}), "fail", "missing_seal_in")):
            img = np.array(render(SEAL_IN_STEP, omit=omit, label=False).convert("RGB"))[:, :, ::-1].copy()
            frame = camera(img, 0, working=False, rng=rng)
            name = f"{kind}_{i}.jpg"
            cv2.imwrite(str(out / name), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            rows.append({"file": name, "step_id": "s7_seal_in", "label": label, "tag": tag})
    with open(out / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "step_id", "label", "tag"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} images + labels.csv to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "eval/data/smoke"))
