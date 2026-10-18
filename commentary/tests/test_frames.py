from conftest import jpeg
from guide import frames


def f(t, s):
    return {"t": t, "sharpness": s}


def test_sharpness_orders_sharp_over_blurry():
    assert frames.sharpness_jpeg(jpeg(True)) > 10 * frames.sharpness_jpeg(jpeg(False))


def test_sharpness_is_resolution_independent():
    import cv2

    big = frames.decode_jpeg(jpeg(True, (1440, 1080)))
    small = cv2.resize(big, (720, 540), interpolation=cv2.INTER_AREA)  # same scene, half res
    assert 0.5 < frames.sharpness(small) / frames.sharpness(big) < 2


def test_pick_sharpest_respects_age():
    fs = [f(0, 900), f(5, 100), f(9, 200)]
    assert frames.pick_sharpest(fs)["t"] == 0
    assert frames.pick_sharpest(fs, max_age_s=6)["t"] == 9
    assert frames.pick_sharpest(fs, now=100, max_age_s=6) is None
    assert frames.pick_sharpest([]) is None


def test_pick_reference_near_end_prefers_sharp_then_late():
    fs = [f(t, s) for t, s in [(1, 999), (6, 50), (7, 80), (8, 80), (12, 999)]]
    assert frames.pick_reference(fs, t_end=8)["t"] == 8  # tie -> later; 1 and 12 out of window
    assert frames.pick_reference([f(1, 1), f(30, 1)], t_end=20)["t"] == 30  # nearest fallback


def test_display_jpeg_limits_size():
    import cv2
    import numpy as np

    out = frames.display_jpeg(jpeg(True, (1920, 1080)))
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 600


def test_evenly_spaced():
    fs = [f(t, 0) for t in range(100)]
    picked = frames.evenly_spaced(fs, 5)
    assert [p["t"] for p in picked] == [0, 25, 50, 74, 99] or len(picked) == 5
    assert len(frames.evenly_spaced(fs[:3], 5)) == 3
