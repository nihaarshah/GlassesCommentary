"""Turn a guided run into a short demo video (MP4) from its real data.

For each check: the glasses screen (step + verdict), the expert's reference,
the frame the glasses camera actually sent, and the checker's reason and
latency. Then a summary and, optionally, a screenshot of the work record.
Nothing is invented: every image, verdict and number comes from the run.

  cd commentary
  python -m guide.tools.run_video run_962b3fbf0d out.mp4 --since 07:51:00 \
      --record-shot record.png --title "Hands-free guided wiring, every step verified"

--since skips attempts before a local clock time (e.g. setup/aiming checks);
the summary says so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from guide import ffmpeg

API = "https://nihaarshah--glasses-guide-api.us-east.modal.run"
W, H = 1920, 1080
BG, INK, MUTED = (14, 14, 16), (245, 245, 245), (160, 164, 170)
COLORS = {"pass": (52, 199, 89), "fail": (229, 57, 53), "unsure": (255, 179, 0), "hold_still": (255, 179, 0), "error": (255, 179, 0)}
LABELS = {"pass": "Step verified", "fail": "Issue caught", "unsure": "Look closer", "hold_still": "Hold still", "error": "Try again"}
SYMBOL_FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"  # Arial has no ✓ / ⚠ glyphs


def symbol(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(SYMBOL_FONT, size)
    except OSError:
        return font(size, True)


def draw_icon(d: ImageDraw.ImageDraw, x: int, y: int, size: int, verdict: str, color) -> None:
    """✓ for a pass, else a drawn warning triangle (no common font has ⚠)."""
    if verdict == "pass":
        d.text((x, y), "✓", font=symbol(size), fill=color)
        return
    h = int(size * 0.9)
    top, left, right = (x + size // 2, y + 4), (x, y + h), (x + size, y + h)
    d.polygon([top, right, left], outline=color, width=max(3, size // 12))
    d.text((x + size // 2, y + h - size * 0.12), "!", font=font(int(size * 0.55), True), fill=color, anchor="ms")


def full_reason(reason: str) -> str:
    reason = reason.strip()
    return reason + "…" if reason and reason[-1] not in ".!?" else reason  # cut at the 12-word limit


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]
        if bold
        else ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    )
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def img(data: bytes | None) -> Image.Image | None:
    return Image.open(io.BytesIO(data)).convert("RGB") if data else None


def fit(im: Image.Image, box: tuple[int, int]) -> Image.Image:
    im = im.copy()
    scale = min(box[0] / im.width, box[1] / im.height)
    return im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        nxt = f"{cur} {w}".strip()
        if d.textlength(nxt, font=f) <= width:
            cur = nxt
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_block(d, xy, text, f, width, fill=INK, spacing=8) -> int:
    x, y = xy
    for line in wrap(d, text, f, width):
        d.text((x, y), line, font=f, fill=fill)
        y += f.size + spacing
    return y


def glasses_screen(step_no: int, total: int, step: dict, attempt: dict | None) -> Image.Image:
    """A rendering of the 600x600 glasses screen for this moment (black = transparent)."""
    g = Image.new("RGB", (600, 600), (0, 0, 0))
    d = ImageDraw.Draw(g)
    if attempt is None or attempt["verdict"] == "pass":
        if attempt:
            d.text((300, 230), "✓", font=symbol(150), fill=COLORS["pass"], anchor="mm")
            text_block(d, (40, 340), full_reason(attempt["reason"]), font(30, True), 520)
        else:
            d.text((24, 30), f"{step_no} of {total}", font=font(26, True), fill=MUTED)
            text_block(d, (24, 70), step["title"], font(36, True), 550)
            text_block(d, (24, 130), step["instruction"], font(28), 550)
        return g
    title = {"fail": "Not yet", "unsure": "Look closer", "hold_still": "Hold still"}.get(attempt["verdict"], "Try again")
    draw_icon(d, 24, 36, 44, attempt["verdict"], (255, 204, 0))
    d.text((80, 40), title, font=font(40, True), fill=(255, 204, 0))
    text_block(d, (24, 110), full_reason(attempt["reason"]), font(32, True), 550)
    for i, label in enumerate(["Re-check", "Override", "Back"]):
        x = 24 + i * 188
        d.rounded_rectangle((x, 500, x + 176, 580), radius=16, fill=(28, 30, 33), outline=(255, 255, 255) if i == 0 else None, width=3)
        d.text((x + 88, 540), label, font=font(26, True), fill=INK, anchor="mm")
    return g


def panel(canvas: Image.Image, box: tuple[int, int, int, int], im: Image.Image | None, caption: str) -> None:
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(canvas)
    d.text((x0, y0), caption, font=font(26, True), fill=MUTED)
    inner = (x0, y0 + 40, x1, y1)
    d.rounded_rectangle(inner, radius=18, fill=(28, 30, 33))
    if im is not None:
        f = fit(im, (x1 - x0 - 24, y1 - y0 - 64))
        canvas.paste(f, (x0 + (x1 - x0 - f.width) // 2, y0 + 40 + (y1 - y0 - 40 - f.height) // 2))
    else:
        d.text(((x0 + x1) // 2, (y0 + y1) // 2), "no frame", font=font(28), fill=MUTED, anchor="mm")


def step_slide(proc_title, step_no, total, step, attempt, ref, shot, note=None) -> Image.Image:
    c = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(c)
    d.text((60, 40), proc_title.upper(), font=font(24, True), fill=MUTED)
    d.text((60, 76), f"Step {step_no} of {total}:  {step['title']}", font=font(52, True), fill=INK)
    d.text((60, 146), f"Check: {step['check']}", font=font(26), fill=MUTED)
    panel(c, (60, 210, 640, 830), glasses_screen(step_no, total, step, attempt), "Guide screen (glasses display layout)")
    panel(c, (680, 210, 1260, 830), ref, "Expert reference")
    panel(c, (1300, 210, 1860, 830), shot, "What the glasses camera saw")
    if attempt:
        color = COLORS.get(attempt["verdict"], COLORS["error"])
        d.rounded_rectangle((60, 860, 1860, 1020), radius=22, fill=color)
        draw_icon(d, 96, 878, 46, attempt["verdict"], (10, 10, 10))
        d.text((156, 880), LABELS.get(attempt["verdict"], attempt["verdict"]), font=font(44, True), fill=(10, 10, 10))
        secs = f"{attempt['latency_ms'] / 1000:.1f} s" if attempt.get("latency_ms") else ""
        d.text((1820, 892), f"AI check · {secs}", font=font(28, True), fill=(10, 10, 10), anchor="ra")
        text_block(d, (100, 945), "“" + full_reason(attempt["reason"]) + "”", font(34, True), 1700, fill=(10, 10, 10))
    if note:
        d.text((1860, 76), note, font=font(30, True), fill=(255, 204, 0), anchor="ra")
    return c


def card(lines: list[tuple[str, int, tuple]], sub: str | None = None) -> Image.Image:
    c = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(c)
    y = 300 if not sub else 260
    for text, size, color in lines:
        for line in wrap(d, text, font(size, True), 1600):
            d.text((W // 2, y), line, font=font(size, True), fill=color, anchor="ma")
            y += size + 22
    if sub:
        text_block(d, (260, y + 30), sub, font(32), 1400, fill=MUTED)
    return c


def flow_card() -> Image.Image:
    c = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(c)
    d.text((W // 2, 120), "How it works", font=font(60, True), fill=INK, anchor="ma")
    boxes = [
        ("1", "Expert does the task once", "narrating; glasses record"),
        ("2", "AI writes the guide", "steps, tips, photo checks"),
        ("3", "Novice is guided", "hands-free, on the glasses"),
        ("4", "Every step is checked", "against the expert's photo"),
        ("5", "Work record", "photos, verdicts, times"),
    ]
    bw, gap = 320, 40
    x = (W - (len(boxes) * bw + (len(boxes) - 1) * gap)) // 2
    for n, title, sub in boxes:
        d.rounded_rectangle((x, 380, x + bw, 700), radius=24, fill=(28, 30, 33))
        d.text((x + bw // 2, 420), n, font=font(56, True), fill=COLORS["pass"], anchor="ma")
        yy = text_block(d, (x + 24, 510), title, font(34, True), bw - 48)
        text_block(d, (x + 24, yy + 6), sub, font(26), bw - 48, fill=MUTED)
        x += bw + gap
    return c


def summary_card(proc_title, segment, total_steps, since_note) -> Image.Image:
    passed = {i for i, a in segment if a["verdict"] == "pass"}
    issues = [a for _, a in segment if a["verdict"] == "fail"]
    looks = [a for _, a in segment if a["verdict"] in ("unsure", "hold_still")]
    lat = sorted(a["latency_ms"] for _, a in segment if a.get("latency_ms"))
    span = segment[-1][1]["at"] - segment[0][1]["at"] if segment else 0
    c = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(c)
    d.text((W // 2, 110), "Run summary", font=font(60, True), fill=INK, anchor="ma")
    d.text((W // 2, 190), proc_title, font=font(32), fill=MUTED, anchor="ma")
    stats = [
        (f"{len(passed)}/{total_steps}", "steps verified", COLORS["pass"]),
        (str(len(issues)), "issue caught" if len(issues) == 1 else "issues caught", COLORS["fail"]),
        (str(len(looks)), "“look closer” prompts", COLORS["unsure"]),
        (f"{lat[len(lat) // 2] / 1000:.1f} s" if lat else "–", "median check time", INK),
        (f"{int(span // 60)}:{int(span % 60):02d}", "first check to done", INK),
    ]
    bw, gap = 320, 30
    x = (W - (len(stats) * bw + (len(stats) - 1) * gap)) // 2
    for big, label, color in stats:
        d.rounded_rectangle((x, 330, x + bw, 600), radius=24, fill=(28, 30, 33))
        d.text((x + bw // 2, 390), big, font=font(84, True), fill=color, anchor="ma")
        text_block(d, (x + 24, 510), label, font(28), bw - 48, fill=MUTED)
        x += bw + gap
    if issues:
        text_block(d, (260, 680), "Caught: “" + full_reason(issues[0]["reason"]) + "”", font(36, True), 1400, fill=INK)
    if since_note:
        d.text((W // 2, 1000), since_note, font=font(24), fill=MUTED, anchor="ma")
    return c


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("out")
    p.add_argument("--api", default=API)
    p.add_argument("--since", help="local HH:MM:SS; skip earlier attempts (setup/aiming)")
    p.add_argument("--record-shot", type=Path, help="screenshot of the work record to show near the end")
    p.add_argument("--title", default="Hands-free guided work, every step verified")
    a = p.parse_args()
    api = a.api.rstrip("/")

    run = json.loads(fetch(f"{api}/runs/{a.run_id}"))
    proc = json.loads(fetch(f"{api}/procedures/{run['procedure_id']}"))
    steps = {s["id"]: s for s in proc["steps"]}
    total = len(run["steps"])
    day = dt.datetime.fromtimestamp(run["started_at"]).date()
    since_ts = dt.datetime.combine(day, dt.time.fromisoformat(a.since)).timestamp() if a.since else None

    refs: dict[str, Image.Image | None] = {}
    slides: list[tuple[Image.Image, float]] = [
        (card([(a.title, 72, INK), ("Ray-Ban Display glasses camera · AI step checks · auto work record", 36, MUTED)]), 4.0),
        (flow_card(), 6.0),
    ]
    segment = []
    for i, rs in enumerate(run["steps"], 1):
        step = steps[rs["step_id"]]
        if step["id"] not in refs:
            refs[step["id"]] = img(fetch(f"{api}/media/{step['reference_image']}")) if step.get("reference_image") else None
        attempts = [x for x in rs["attempts"] if since_ts is None or x["at"] >= since_ts]
        attempts = [x for x in attempts if x.get("image")]  # skip "no camera frames" errors
        if not attempts:
            continue
        slides.append((step_slide(proc["title"], i, total, step, None, refs[step["id"]], None), 2.2))
        caught_before = False
        for att in attempts:
            segment.append((i, att))
            shot = img(fetch(f"{api}/media/{att['image']}"))
            note = None
            if att["verdict"] == "fail":
                note, caught_before = "Planted mistake", True
            elif att["verdict"] == "pass" and caught_before:
                note = "Corrected"
            dur = {"pass": 3.0, "fail": 5.5}.get(att["verdict"], 3.5)
            slides.append((step_slide(proc["title"], i, total, step, att, refs[step["id"]], shot, note), dur))

    since_note = f"Checks from {a.since} onward (earlier setup/aiming checks omitted). Full log in the work record." if a.since else None
    slides.append((summary_card(proc["title"], segment, total, since_note), 6.0))
    if a.record_shot and a.record_shot.exists():
        c = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(c)
        d.text((W // 2, 40), "Work record, generated automatically", font=font(44, True), fill=INK, anchor="ma")
        shot = fit(Image.open(a.record_shot).convert("RGB"), (W - 160, H - 140))
        c.paste(shot, ((W - shot.width) // 2, 120))
        slides.append((c, 6.0))
    slides.append((card([("Every step guided. Every step checked.", 64, INK), ("Every mistake on record.", 64, COLORS["pass"])]), 4.0))

    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp, "list.txt")
        with open(lst, "w") as f:
            for n, (im, dur) in enumerate(slides):
                path = Path(tmp, f"{n:03d}.png")
                im.save(path)
                f.write(f"file '{path}'\nduration {dur}\n")
            f.write(f"file '{Path(tmp, f'{len(slides) - 1:03d}.png')}'\n")  # concat demuxer needs the last file twice
        subprocess.run(
            [ffmpeg.exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-movflags", "+faststart", a.out],
            check=True,
        )  # fmt: skip
    total_s = sum(d for _, d in slides)
    print(f"wrote {a.out}: {len(slides)} slides, {total_s:.0f} s")


if __name__ == "__main__":
    main()
