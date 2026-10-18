"""Render labeled wiring diagrams for the seed procedure (one per step, cumulative).

These are stand-ins for the expert's reference photos so the guide screens can be
built before a real capture exists. Run from the repo root:
  python commentary/guide/seed/render_diagrams.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "din_rail_start_stop"
W, H = 600, 420
RED, BLUE, GREY = (220, 40, 40), (40, 90, 230), (90, 90, 90)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# Device boxes on the rail and their terminal positions (x, y).
DEVICES = {
    "TB": {"label": "Terminals", "box": (20, 120, 110, 300), "terms": {"+24V": (65, 160), "0V": (65, 260)}},
    "STOP": {"label": "Stop", "box": (140, 120, 230, 300), "terms": {"11": (185, 160), "12": (185, 260)}},
    "START": {"label": "Start", "box": (260, 120, 350, 300), "terms": {"13": (305, 160), "14": (305, 260)}},
    "K1": {
        "label": "K1",
        "box": (380, 120, 490, 300),
        "terms": {"A1": (410, 160), "13": (460, 160), "A2": (410, 260), "14": (460, 260)},
    },
    "H1": {"label": "H1", "box": (510, 120, 580, 300), "terms": {"X1": (545, 160), "X2": (545, 260)}},
}

# (step, from(device, term), to(device, term), color, lane_a, lane_b, gap_x)
# Wires leave top terminals upward and bottom terminals downward, run in their own
# lane (y) outside the device boxes, and change sides in a gap between devices, so
# no wire ever crosses a terminal it isn't connected to.
WIRES = [
    (2, ("TB", "+24V"), ("STOP", "11"), RED, 100, 100, None),
    (3, ("STOP", "12"), ("START", "13"), RED, 320, 100, 245),
    (4, ("START", "14"), ("K1", "A1"), RED, 335, 85, 365),
    (5, ("K1", "A2"), ("TB", "0V"), BLUE, 380, 380, None),
    (6, ("H1", "X1"), ("K1", "A1"), RED, 70, 70, None),
    (6, ("H1", "X2"), ("K1", "A2"), BLUE, 350, 350, None),
    (7, ("K1", "13"), ("START", "13"), RED, 55, 55, None),
    (7, ("K1", "14"), ("K1", "A1"), RED, 365, 40, 500),
]


def pos(dev: str, term: str) -> tuple[int, int]:
    return DEVICES[dev]["terms"][term]


def draw_wire(d: ImageDraw.ImageDraw, a, b, color, lane_a: int, lane_b: int, gap, width: int) -> None:
    (x1, y1), (x2, y2) = a, b
    if gap is None:
        pts = [(x1, y1), (x1, lane_a), (x2, lane_a), (x2, y2)]
    else:
        pts = [(x1, y1), (x1, lane_a), (gap, lane_a), (gap, lane_b), (x2, lane_b), (x2, y2)]
    d.line(pts, fill=color, width=width, joint="curve")


def render(step: int, omit: frozenset[int] = frozenset(), label: bool = True) -> Image.Image:
    """Diagram after `step`, without the wires added in steps listed in `omit`."""
    img = Image.new("RGB", (W, H), (245, 245, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 200, W, 220), fill=(170, 170, 170))  # DIN rail
    for dev in DEVICES.values():
        d.rounded_rectangle(dev["box"], radius=8, fill=(225, 225, 225), outline=GREY, width=3)
        x0, y0, x1, _ = dev["box"]
        d.text(((x0 + x1) // 2, y0 + 70), dev["label"], fill=(30, 30, 30), font=font(18), anchor="mm")
        for name, (x, y) in dev["terms"].items():
            d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(250, 250, 250), outline=(30, 30, 30), width=2)
            d.text((x, y + (-22 if y < 200 else 22)), name, fill=(0, 0, 0), font=font(16), anchor="mm")
    for s, a, b, color, lane_a, lane_b, gap in WIRES:
        if s <= step and s not in omit:
            draw_wire(d, pos(*a), pos(*b), color, lane_a, lane_b, gap, 7 if s == step else 4)
    if step == 8:
        d.text((W // 2, 405), "All wires seated - tug test", fill=(20, 120, 40), font=font(20), anchor="mm")
    if label:
        d.text((8, 8), f"SEED DIAGRAM - step {step} - replace with expert photo", fill=(120, 120, 120), font=font(14))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for step in range(1, 9):
        render(step).save(OUT / f"step{step}.jpg", quality=85)
    print(f"wrote 8 diagrams to {OUT}")


if __name__ == "__main__":
    main()
