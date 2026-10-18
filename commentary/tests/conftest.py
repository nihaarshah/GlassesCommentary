import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `guide` importable

from guide.store import Store  # noqa: E402

SEED_DIR = Path(__file__).resolve().parents[1] / "guide" / "seed"


@pytest.fixture
def store(tmp_path):
    return Store({}, tmp_path / "media")


@pytest.fixture
def seeded(store):
    from guide.seed_loader import load_seed

    files = {p.relative_to(SEED_DIR).as_posix(): p.read_bytes() for p in SEED_DIR.rglob("*.jpg")}
    proc = load_seed(store, (SEED_DIR / "din_rail_start_stop.json").read_text(), files)
    return store, proc


def jpeg(sharp: bool = True, size=(480, 360)) -> bytes:
    import cv2
    import numpy as np

    w, h = size
    img = np.full((h, w, 3), 128, np.uint8)
    if sharp:
        for x in range(0, w, 12):
            cv2.line(img, (x, 0), (x, h), (255, 255, 255), 2)
    else:
        img = cv2.GaussianBlur(img, (31, 31), 0)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()
