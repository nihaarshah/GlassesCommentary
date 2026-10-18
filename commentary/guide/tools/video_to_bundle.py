"""Turn any narrated video into the capture bundle the phone uploads, and
optionally push it through the deployed pipeline.

  python -m guide.tools.video_to_bundle sample.mp4 out.zip --fps 2
  python -m guide.tools.video_to_bundle sample.mp4 out.zip --upload https://...modal.run --title "My task"

Run from commentary/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from guide import bundle, ffmpeg


def video_to_bundle(video: str, out_zip: str, fps: float = 2.0, max_side: int = 1280, title: str = "Untitled procedure") -> int:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [ffmpeg.exe(), "-y", "-loglevel", "error", "-i", video,
             "-vf", f"fps={fps},scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'",
             "-q:v", "3", str(Path(tmp, "%06d.jpg"))],
            check=True,
        )  # fmt: skip
        frames = []
        for i, p in enumerate(sorted(Path(tmp).glob("*.jpg"))):
            frames.append((i / fps, p.read_bytes()))
        audio = None
        a = Path(tmp, "audio.m4a")
        r = subprocess.run(
            [ffmpeg.exe(), "-y", "-loglevel", "error", "-i", video, "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "64k", str(a)],
        )  # fmt: skip
        if r.returncode == 0 and a.exists() and a.stat().st_size > 0:
            audio = ("audio.m4a", a.read_bytes())
        bundle.write(out_zip, title, frames, audio, started_at=time.time())
        return len(frames)


def _req(method: str, url: str, data: bytes | None = None, ctype: str = "application/json") -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def upload_and_process(api: str, zip_path: str, title: str, poll_s: float = 5.0) -> dict:
    cap = _req("POST", f"{api}/captures/start", json.dumps({"title": title}).encode())
    _req("POST", f"{api}/captures/{cap['id']}/bundle", Path(zip_path).read_bytes(), "application/zip")
    _req("POST", f"{api}/captures/{cap['id']}/process", b"")
    while True:
        st = _req("GET", f"{api}/captures/{cap['id']}")
        print(f"  {st['state']:<10} {st['progress']:.0%} {st['stage']}", flush=True)
        if st["state"] in ("done", "error"):
            return st
        time.sleep(poll_s)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("out_zip")
    p.add_argument("--fps", type=float, default=2.0)
    p.add_argument("--title", default="Untitled procedure")
    p.add_argument("--upload", help="glasses-guide API base URL; uploads and runs the pipeline")
    a = p.parse_args()
    n = video_to_bundle(a.video, a.out_zip, a.fps, title=a.title)
    print(f"wrote {a.out_zip} ({n} frames)")
    if a.upload:
        st = upload_and_process(a.upload.rstrip("/"), a.out_zip, a.title)
        print(json.dumps(st, indent=2))


if __name__ == "__main__":
    main()
