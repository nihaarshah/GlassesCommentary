"""Modal app `glasses-guide`: capture -> procedure -> guided, verified runs.

Separate from commentary/modal_app.py (live Moondream commentary), which this
app never touches.

Deploy (from commentary/):
  modal deploy -m guide.modal_app
Seed the demo procedure:
  modal run -m guide.modal_app::seed
Needs a Modal secret named `anthropic` with ANTHROPIC_API_KEY (M2/M3 only).
"""

from __future__ import annotations

from pathlib import Path

import modal

from guide import config

app = modal.App("glasses-guide")

STATE = modal.Dict.from_name("glasses-guide-state", create_if_missing=True)
MEDIA = modal.Volume.from_name("glasses-guide-media", create_if_missing=True)
MEDIA_ROOT = "/media"

SEED_DIR = Path(__file__).parent / "seed"

api_image = (
    # Bookworm explicitly: the default debian_slim here is bullseye, whose security
    # archive has moved and now 404s on apt installs.
    modal.Image.from_registry("python:3.11-slim-bookworm")
    .apt_install("libgl1", "libglib2.0-0", "ffmpeg")
    .pip_install(
        "fastapi[standard]",
        "pydantic>=2",
        "numpy",
        "opencv-python-headless",
        "pillow",
        "anthropic",
    )
    .add_local_python_source("guide")
)

whisper_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper==1.1.1", "requests")
    # Bake the weights into the image so cold starts don't download ~3 GB.
    .run_commands(f"python -c \"from faster_whisper.utils import download_model; download_model('{config.WHISPER_MODEL}')\"")
    .add_local_python_source("guide")
)


def _store():
    from guide.store import Store

    return Store(STATE, Path(MEDIA_ROOT), commit=MEDIA.commit, reload=MEDIA.reload)


def _llm_secrets() -> list[modal.Secret]:
    """Attach the LLM secret only if it exists, so M1 deploys before the key is set.
    Redeploy after `modal secret create anthropic ANTHROPIC_API_KEY=...`."""
    secret = modal.Secret.from_name(config.LLM_SECRET_NAME)
    try:
        secret.hydrate()
        return [secret]
    except Exception:  # noqa: BLE001 -- not created yet; /check and /process report it
        return []


@app.function(
    image=api_image,
    volumes={MEDIA_ROOT: MEDIA},
    secrets=_llm_secrets(),
    timeout=120,
    scaledown_window=600,
    min_containers=0,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from guide.api import Services, create_app

    def spawn(cap_id: str) -> None:
        process_capture.spawn(cap_id)

    verify = None
    try:
        from guide.verifier import make_verifier

        verify = make_verifier()
    except Exception as exc:  # noqa: BLE001 -- surfaced as 503 on /check
        print(f"[guide] verifier unavailable: {exc}")

    return create_app(_store(), Services(verify=verify, spawn_pipeline=spawn))


@app.function(image=whisper_image, gpu="L4", timeout=900, volumes={MEDIA_ROOT: MEDIA})
def transcribe(audio_rel: str) -> dict:
    from guide.transcribe import transcribe_file

    MEDIA.reload()
    return transcribe_file(str(Path(MEDIA_ROOT) / audio_rel), config.WHISPER_MODEL)


@app.function(
    image=api_image,
    volumes={MEDIA_ROOT: MEDIA},
    secrets=_llm_secrets(),
    timeout=1800,
)
def process_capture(cap_id: str) -> None:
    from guide.pipeline import run_pipeline

    MEDIA.reload()
    run_pipeline(_store(), cap_id, transcribe=lambda rel: transcribe.remote(rel))


@app.function(image=api_image, volumes={MEDIA_ROOT: MEDIA})
def seed_remote(files: dict[str, bytes], procedure_json: str) -> str:
    from guide.seed_loader import load_seed

    proc = load_seed(_store(), procedure_json, files)
    return proc.id


@app.local_entrypoint()
def seed() -> None:
    """Upload seed/<name>.json and its images, then publish it."""
    files = {
        p.relative_to(SEED_DIR).as_posix(): p.read_bytes()
        for p in SEED_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4")
    }
    for js in sorted(SEED_DIR.glob("*.json")):
        pid = seed_remote.remote(files, js.read_text())
        print(f"seeded {js.name} -> {pid}")


@app.function(image=api_image, secrets=_llm_secrets(), timeout=120)
def verify_remote(reference_jpeg: bytes | None, frame_jpeg: bytes, check_text: str) -> dict:
    """The production verifier, callable from eval/run_eval.py."""
    from guide.verifier import make_verifier

    return make_verifier()(reference_jpeg, frame_jpeg, check_text)


@app.function(image=api_image, secrets=_llm_secrets(), timeout=120)
def read_labels_remote(frame_jpeg: bytes) -> dict:
    """M0 spike 3: what terminal labels and wire colors can the model read?"""
    from guide import frames
    from guide.llm import ImagePart, make_llm

    schema = {
        "type": "object",
        "properties": {
            "terminals": {"type": "array", "items": {"type": "string"}},
            "wires": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"color": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}},
                    "required": ["color", "from", "to"],
                    "additionalProperties": False,
                },
            },
            "unreadable": {"type": "string"},
        },
        "required": ["terminals", "wires", "unreadable"],
        "additionalProperties": False,
    }
    return make_llm().json(
        system="List every terminal label you can read in this photo exactly as printed, and every wire "
        "you can trace (color, from terminal, to terminal). Put anything you can't read in 'unreadable'. "
        "Don't guess.",
        parts=[ImagePart(frames.display_jpeg(frame_jpeg, max_side=1568, quality=90))],
        schema=schema,
        model=config.VERIFY_MODEL,
        effort="medium",
        max_tokens=4000,
        timeout_s=60,
    )
