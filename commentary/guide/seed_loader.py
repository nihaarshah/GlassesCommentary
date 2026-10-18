"""Load a seed procedure (JSON + images) into the store.

Seed JSON references images by path relative to guide/seed/; they're stored
under media `seed/<path>` and the step fields are rewritten to match.
"""

from __future__ import annotations

import json

from .frames import display_jpeg
from .schemas import Procedure
from .store import Store


def load_seed(store: Store, procedure_json: str, files: dict[str, bytes]) -> Procedure:
    raw = json.loads(procedure_json)
    raw.pop("_comment", None)

    def put(rel: str | None) -> str | None:
        if not rel:
            return None
        if rel not in files:
            raise FileNotFoundError(f"seed file missing: {rel}")
        data = files[rel]
        if rel.lower().endswith((".jpg", ".jpeg")):
            data = display_jpeg(data)
        return store.put_media(f"seed/{rel}", data, commit=False)

    for step in raw["steps"]:
        step["reference_image"] = put(step.get("reference_image"))
        step["clip"] = put(step.get("clip"))
        step["keyframes"] = [put(k) for k in step.get("keyframes", [])]
    store.commit_media()

    proc = Procedure.model_validate(raw)
    store.save_procedure(proc)
    return proc
