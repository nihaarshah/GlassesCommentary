"""Which ffmpeg to run: $FFMPEG, else the imageio-ffmpeg static build if installed
(local dev), else `ffmpeg` on PATH (the Modal image apt-installs it)."""

from __future__ import annotations

import os
from functools import cache


@cache
def exe() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 -- not installed: use PATH
        return "ffmpeg"
