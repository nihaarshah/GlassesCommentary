"""FastAPI routes for capture, review, guided runs and records.

`create_app(store, services)` is pure so tests can drive it with an in-memory
store. On Modal, modal_app.py builds it with a modal.Dict + modal.Volume and
real services (pipeline spawner, verifier).
"""

from __future__ import annotations

import mimetypes
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import config, frames
from .schemas import CaptureStatus, CheckAttempt, Override, Procedure, Run, new_id, now
from .store import Store


@dataclass
class Services:
    """Side-effectful collaborators, injected so the routes stay testable.

    verify(reference_jpeg | None, frame_jpeg, check_text) -> {"verdict", "reason"}
    spawn_pipeline(capture_id) -> None
    """

    verify: Callable[[bytes | None, bytes, str], dict] | None = None
    spawn_pipeline: Callable[[str], None] | None = None


class StartRunBody(BaseModel):
    procedure_id: str
    operator: str | None = None


class OverrideBody(BaseModel):
    note: str = "Operator override"


class StartCaptureBody(BaseModel):
    title: str = "Untitled procedure"


class AnswersBody(BaseModel):
    answers: dict[str, str]  # followup id -> answer


def create_app(store: Store, services: Services | None = None) -> FastAPI:
    services = services or Services()
    app = FastAPI(title="glasses-guide")
    # The glasses web app and review page are served from vercel.app.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    # ------------------------------------------------------------------ helpers

    def require_procedure(proc_id: str) -> Procedure:
        proc = store.get_procedure(proc_id)
        if not proc:
            raise HTTPException(404, "procedure not found")
        return proc

    def require_run(run_id: str) -> Run:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return run

    def run_view(run: Run) -> dict:
        proc = store.get_procedure(run.procedure_id)
        out: dict[str, Any] = run.model_dump(mode="json")
        out["total_steps"] = len(run.steps)
        out["issues_caught"] = run.issues_caught()
        last = run.last_attempt
        out["last_attempt"] = last.model_dump(mode="json") if last else None
        if proc and run.screen != "done":
            out["step"] = proc.steps[run.current_step].model_dump(mode="json")
        return out

    def advance(run: Run) -> Run:
        t = now()
        run.steps[run.current_step].ended_at = t
        if run.current_step + 1 >= len(run.steps):
            # Stays the active run so the glasses keep showing Done until Home.
            run.screen = "done"
            run.ended_at = t
        else:
            run.current_step += 1
            run.steps[run.current_step].started_at = run.steps[run.current_step].started_at or t
            run.screen = "step"
        return run

    # ------------------------------------------------------------------ health / display

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "t": time.time()}

    @app.get("/display")
    def display() -> dict:
        """Everything the glasses web app needs for its current screen, polled ~1 Hz."""
        cap_id = store.active_capture_id()
        if cap_id:
            cap = store.get_capture(cap_id)
            if cap and cap.state == "recording":
                return {"mode": "recording", "capture": cap.model_dump(mode="json"), "server_t": now()}
        run_id = store.active_run_id()
        if run_id:
            run = store.get_run(run_id)
            if run:
                view = run_view(run)
                req = store.photo_request(run.id)
                return {"mode": "run", "run": view, "photo_request": req, "server_t": now()}
        procs = [p.summary() for p in store.list_procedures() if p.status == "published"]
        return {"mode": "home", "procedures": procs, "last_run": store.get_setting("last_run"), "server_t": now()}

    # ------------------------------------------------------------------ media

    @app.get("/media/{path:path}")
    def media(path: str) -> Response:
        try:
            data = store.read_media(path)
        except ValueError:
            raise HTTPException(400, "bad path")
        if data is None:
            raise HTTPException(404, "not found")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=3600"})

    # ------------------------------------------------------------------ procedures

    @app.get("/procedures")
    def list_procedures() -> list[dict]:
        return [p.summary() for p in store.list_procedures()]

    @app.get("/procedures/{proc_id}")
    def get_procedure(proc_id: str) -> dict:
        return require_procedure(proc_id).model_dump(mode="json")

    @app.put("/procedures/{proc_id}")
    def put_procedure(proc_id: str, body: dict = Body(...)) -> dict:
        """Review-page save: full replacement (edits, reorder, merge, delete)."""
        existing = require_procedure(proc_id)
        body = {**body, "id": proc_id, "created_at": existing.created_at, "updated_at": now()}
        try:
            proc = Procedure.model_validate(body)
        except Exception as exc:  # pydantic.ValidationError -> 422 with readable text
            raise HTTPException(422, str(exc))
        store.save_procedure(proc)
        return proc.model_dump(mode="json")

    @app.post("/procedures/{proc_id}/answers")
    def answer_followups(proc_id: str, body: AnswersBody) -> dict:
        """Answers to follow-up questions become tips on the step they refer to
        (or the first step if the question isn't tied to one)."""
        proc = require_procedure(proc_id)
        steps_by_id = {s.id: s for s in proc.steps}
        for fq in proc.followups:
            answer = (body.answers.get(fq.id) or "").strip()
            if not answer or answer == fq.answer:
                continue
            target = steps_by_id.get(fq.step_id or "") or proc.steps[0]
            if fq.answer and fq.answer in target.tips:
                target.tips.remove(fq.answer)
            target.tips.append(answer)
            fq.answer = answer
        proc.updated_at = now()
        store.save_procedure(proc)
        return proc.model_dump(mode="json")

    @app.post("/procedures/{proc_id}/publish")
    def publish(proc_id: str) -> dict:
        proc = require_procedure(proc_id)
        proc.status = "published"
        proc.updated_at = now()
        store.save_procedure(proc)
        return proc.summary()

    @app.post("/procedures/{proc_id}/unpublish")
    def unpublish(proc_id: str) -> dict:
        proc = require_procedure(proc_id)
        proc.status = "draft"
        store.save_procedure(proc)
        return proc.summary()

    @app.delete("/procedures/{proc_id}")
    def delete_procedure(proc_id: str) -> dict:
        require_procedure(proc_id)
        store.delete_procedure(proc_id)
        return {"deleted": proc_id}

    # ------------------------------------------------------------------ captures (M2)

    @app.post("/captures/start")
    def start_capture(body: StartCaptureBody | None = None) -> dict:
        cap = CaptureStatus(title=(body.title if body else "Untitled procedure"))
        store.save_capture(cap)
        store.set_active_capture(cap.id)
        return cap.model_dump(mode="json")

    @app.post("/captures/{cap_id}/stop")
    def stop_capture(cap_id: str) -> dict:
        cap = store.get_capture(cap_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        if store.active_capture_id() == cap_id:
            store.set_active_capture(None)
        if cap.state == "recording":
            cap.state = "uploaded" if store.read_media(f"captures/{cap_id}/bundle.zip") else "recording"
            store.save_capture(cap)
        return cap.model_dump(mode="json")

    @app.post("/captures/{cap_id}/bundle")
    async def upload_bundle(cap_id: str, request: Request) -> dict:
        """Raw zip body (application/zip): frames/<ms>.jpg, audio.m4a|wav, manifest.json."""
        cap = store.get_capture(cap_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        data = await request.body()
        if not data.startswith(b"PK"):
            raise HTTPException(400, "body must be a zip")
        store.put_media(f"captures/{cap_id}/bundle.zip", data)
        cap.state = "uploaded"
        store.save_capture(cap)
        if store.active_capture_id() == cap_id:
            store.set_active_capture(None)
        return cap.model_dump(mode="json")

    @app.post("/captures/{cap_id}/process")
    def process_capture(cap_id: str) -> dict:
        cap = store.get_capture(cap_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        if cap.state not in ("uploaded", "error", "done"):
            raise HTTPException(409, f"capture is {cap.state}")
        if not services.spawn_pipeline:
            raise HTTPException(503, "pipeline not configured")
        cap.state, cap.progress, cap.stage, cap.error = "processing", 0.0, "queued", None
        store.save_capture(cap)
        services.spawn_pipeline(cap_id)
        return cap.model_dump(mode="json")

    @app.get("/captures/{cap_id}")
    def get_capture(cap_id: str) -> dict:
        cap = store.get_capture(cap_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        return cap.model_dump(mode="json")

    # ------------------------------------------------------------------ runs

    @app.put("/operator")
    def set_operator(body: dict = Body(...)) -> dict:
        """The phone sets who is wearing the glasses; new runs use it."""
        name = str(body.get("operator") or "").strip()[:60] or "Operator"
        store.set_setting("operator", name)
        return {"operator": name}

    @app.post("/runs")
    def start_run(body: StartRunBody) -> dict:
        proc = require_procedure(body.procedure_id)
        operator = body.operator or store.get_setting("operator", "Operator")
        run = Run.start(proc, operator)
        store.save_run(run)
        store.set_active_run(run.id)
        return run_view(run)

    @app.get("/runs")
    def list_runs() -> list[dict]:
        return [
            {
                "id": r.id,
                "procedure_title": r.procedure_title,
                "operator": r.operator,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "issues_caught": r.issues_caught(),
            }
            for r in store.list_runs()
        ]

    @app.get("/runs/active")
    def active_run() -> dict:
        run_id = store.active_run_id()
        run = store.get_run(run_id) if run_id else None
        if not run:
            raise HTTPException(404, "no active run")
        return run_view(run)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return run_view(require_run(run_id))

    @app.post("/runs/{run_id}/frames")
    async def upload_frame(run_id: str, request: Request) -> dict:
        """Phone Guide mode posts a raw JPEG body ~1 Hz."""
        run = require_run(run_id)
        if run.screen == "done":
            raise HTTPException(409, "run finished")
        data = await request.body()
        img = frames.decode_jpeg(data)
        if img is None:
            raise HTTPException(400, "body must be a JPEG")
        store.push_frame(
            run_id,
            {"t": now(), "sharpness": frames.sharpness(img), "jpeg": data},
            keep=config.RECENT_FRAMES_KEPT,
        )
        return {"ok": True}

    @app.post("/runs/{run_id}/photo")
    async def upload_photo(run_id: str, request: Request) -> dict:
        """Phone answers a photo request (capturePhoto(.jpeg)) with a raw JPEG body."""
        require_run(run_id)
        data = await request.body()
        if frames.decode_jpeg(data) is None:
            raise HTTPException(400, "body must be a JPEG")
        req = store.photo_request(run_id)
        store.kv[f"photo:{run_id}"] = {"request": req, "t": now(), "jpeg": data}
        store.request_photo(run_id, None)
        return {"ok": True}

    @app.post("/runs/{run_id}/check")
    def check(run_id: str) -> dict:
        run = require_run(run_id)
        if run.screen == "done":
            raise HTTPException(409, "run finished")
        if not services.verify:
            raise HTTPException(503, "verifier not configured")
        from .checking import run_check  # local import keeps M1 importable without it

        run.screen = "checking"
        store.save_run(run)
        run = run_check(store, services.verify, run)
        store.save_run(run)
        return run_view(run)

    @app.post("/runs/{run_id}/next")
    def next_step(run_id: str) -> dict:
        run = require_run(run_id)
        if run.screen == "done":
            return run_view(run)
        run = advance(run)
        store.save_run(run)
        if run.screen == "done":
            store.set_setting("last_run", run.id)
        return run_view(run)

    @app.post("/runs/{run_id}/back")
    def back(run_id: str) -> dict:
        run = require_run(run_id)
        if run.screen == "result":
            run.screen = "step"
        elif run.screen == "step" and run.current_step > 0:
            run.current_step -= 1
            run.screen = "step"
        store.save_run(run)
        return run_view(run)

    @app.post("/runs/{run_id}/override")
    def override(run_id: str, body: OverrideBody | None = None) -> dict:
        run = require_run(run_id)
        if run.screen == "done":
            raise HTTPException(409, "run finished")
        run.steps[run.current_step].override = Override(note=(body.note if body else "Operator override"))
        run = advance(run)
        store.save_run(run)
        if run.screen == "done":
            store.set_setting("last_run", run.id)
        return run_view(run)

    @app.post("/runs/{run_id}/finish")
    def finish(run_id: str) -> dict:
        run = require_run(run_id)
        if run.screen != "done":
            t = now()
            run.steps[run.current_step].ended_at = t
            run.screen, run.ended_at = "done", t
            store.save_run(run)
        store.set_setting("last_run", run.id)
        return run_view(run)

    @app.post("/home")
    def go_home() -> dict:
        """Glasses 'Home' from the Done screen: clear the active run."""
        store.set_active_run(None)
        return {"ok": True}

    return app
