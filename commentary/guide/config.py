"""Backend configuration. Provider/model names live here; API keys only ever come
from Modal secrets (environment variables), never from the repo."""

from __future__ import annotations

import os

# --- hosted multimodal model (step splitting, writing, verification) -------------
LLM_PROVIDER = os.getenv("GUIDE_LLM_PROVIDER", "anthropic")
AUTHOR_MODEL = os.getenv("GUIDE_AUTHOR_MODEL", "claude-opus-5-5")
VERIFY_MODEL = os.getenv("GUIDE_VERIFY_MODEL", "claude-sonnet-5-5")
LLM_SECRET_NAME = "anthropic"  # modal secret create anthropic ANTHROPIC_API_KEY=...
VERIFY_TIMEOUT_S = 12.0

# --- transcription -----------------------------------------------------------------
WHISPER_MODEL = os.getenv("GUIDE_WHISPER_MODEL", "large-v3")

# --- media limits (Bluetooth display) ---------------------------------------------
DISPLAY_IMAGE_MAX_PX = 600  # longest side of anything the glasses show
CLIP_MAX_SECONDS = 5.0
CLIP_MAX_SIDE = 400
CLIP_MAX_PIXELS = 70_000
CLIP_FPS = 8
KEYFRAMES_PER_STEP = 4

# --- guide mode ----------------------------------------------------------------------
RECENT_FRAMES_KEPT = 6  # phone uploads ~1 Hz; Check picks the sharpest of these
FRAME_MAX_AGE_S = 8.0  # older frames are ignored for a check
# Below this Laplacian variance (on a 600 px-wide grayscale frame) we ask the user
# to hold still instead of judging. Tune from M0 frames.
BLUR_THRESHOLD = float(os.getenv("GUIDE_BLUR_THRESHOLD", "60"))
# "stream": sharpest recent frame; "photo": ask the phone for capturePhoto(.jpeg).
CHECK_SOURCE = os.getenv("GUIDE_CHECK_SOURCE", "stream")
PHOTO_WAIT_S = 4.0

# --- capture ----------------------------------------------------------------------
# A capture still "recording" after this long is treated as abandoned so the
# glasses don't stay on REC forever if the phone never uploads.
CAPTURE_STALE_S = 30 * 60

# --- pipeline --------------------------------------------------------------------------
MIN_STEPS = 6
MAX_STEPS = 15
SPLIT_SAMPLE_FRAMES = 24  # frames shown to the model when splitting into steps
