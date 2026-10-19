"""Capture bundle format (what the phone uploads, and what video_to_bundle writes).

  bundle.zip
    manifest.json   {"version": 1, "title": str, "started_at": unix s,
                     "frames": [{"file": "frames/000000.jpg", "t": 0.0}, ...],
                     "audio": "audio.m4a" | "audio.wav" | null,
                     "audio_offset_s": 0.0}   # audio t=0 relative to frame t=0
    frames/*.jpg
    audio.m4a
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Bundle:
    root: Path
    title: str
    frames: list[dict]  # [{"t": float, "path": str}]
    audio: Path | None
    audio_offset_s: float


def extract(zip_path: str | Path, dest: str | Path) -> Bundle:
    dest = Path(dest)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            target = (dest / name).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise ValueError(f"unsafe path in bundle: {name}")
        zf.extractall(dest)
    # iOS's system zipper (NSFileCoordinator .forUploading) wraps everything in a
    # top-level folder, so accept manifest.json at the root or one level down.
    candidates = [dest / "manifest.json", *sorted(dest.glob("*/manifest.json"))]
    found = next((m for m in candidates if m.exists()), None)
    if found is None:
        raise ValueError("bundle has no manifest.json")
    dest = found.parent
    manifest = json.loads(found.read_text())
    frames = sorted(
        ({"t": float(f["t"]), "path": str(dest / f["file"])} for f in manifest["frames"]),
        key=lambda f: f["t"],
    )
    if not frames:
        raise ValueError("bundle has no frames")
    audio = dest / manifest["audio"] if manifest.get("audio") else None
    return Bundle(
        root=dest,
        title=manifest.get("title") or "Untitled procedure",
        frames=frames,
        audio=audio if audio and audio.exists() else None,
        audio_offset_s=float(manifest.get("audio_offset_s") or 0.0),
    )


def write(zip_path: str | Path, title: str, frames: list[tuple[float, bytes]], audio: tuple[str, bytes] | None, started_at: float, audio_offset_s: float = 0.0) -> None:
    entries = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for i, (t, jpeg) in enumerate(frames):
            name = f"frames/{i:06d}.jpg"
            zf.writestr(name, jpeg)
            entries.append({"file": name, "t": round(t, 3)})
        if audio:
            zf.writestr(audio[0], audio[1])
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "title": title,
                    "started_at": started_at,
                    "frames": entries,
                    "audio": audio[0] if audio else None,
                    "audio_offset_s": audio_offset_s,
                }
            ),
        )
