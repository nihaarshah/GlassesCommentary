"""Modal-hosted live scene commentary: Moondream2 captions one frame at a time.

Deploy:
  modal deploy commentary/modal_app.py
Then export COMMENTARY_URL=<endpoint from the deploy output> and run client.py.
"""

from __future__ import annotations

from typing import Any

import modal

app = modal.App("glasses-commentary")

# Shared across containers so the glasses webapp (a separate GET-only client)
# can poll for whatever the laptop/phone client most recently POSTed.
STATE = modal.Dict.from_name("glasses-commentary-state", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "torch",
        "torchvision",
        "transformers==4.44.0",
        "einops",
        "pillow",
        "opencv-python-headless",
        "numpy",
        "fastapi[standard]",
    )
)


@app.cls(image=image, gpu="T4", timeout=120, scaledown_window=120)
class Commentary:
    @modal.enter()
    def load(self) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_id = "vikhyatk/moondream2"
        revision = "2024-08-26"
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, revision=revision, trust_remote_code=True
        ).to("cuda")
        self.model.eval()

    @modal.fastapi_endpoint(method="POST")
    def caption(self, body: dict[str, Any]) -> dict[str, Any]:
        import base64

        import cv2
        import numpy as np
        from PIL import Image

        image_b64 = body.get("image_b64") or ""
        prompt = body.get("prompt") or "Describe what you see in one short sentence."

        raw = base64.b64decode(image_b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"error": "bad_image"}
        pil = Image.fromarray(frame[:, :, ::-1])

        enc = self.model.encode_image(pil)
        answer = self.model.answer_question(enc, prompt, self.tokenizer)

        import time

        STATE["latest"] = {"commentary": answer, "t": time.time()}
        return {"commentary": answer}

    @modal.fastapi_endpoint(method="GET")
    def latest(self) -> Any:
        from fastapi.responses import JSONResponse

        data = STATE.get("latest") or {"commentary": "", "t": 0}
        # CORS: the glasses webapp fetches this from a different origin (vercel.app).
        return JSONResponse(content=data, headers={"Access-Control-Allow-Origin": "*"})


@app.local_entrypoint()
def main() -> None:
    print("Deploy with: modal deploy commentary/modal_app.py")
    print("Then export COMMENTARY_URL=<endpoint from the Modal dashboard>")
