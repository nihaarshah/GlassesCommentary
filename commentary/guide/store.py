"""State (modal.Dict) + media (modal.Volume) access behind one small class.

On Modal, `kv` is a modal.Dict and `media_root` is the Volume mount; tests pass
a plain dict and a temp dir. Volume writes need commit() to be visible to other
containers, and readers reload() on a miss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, MutableMapping

from .schemas import CaptureStatus, Procedure, Run


def _noop() -> None:
    return None


class Store:
    def __init__(
        self,
        kv: MutableMapping[str, Any],
        media_root: Path,
        commit: Callable[[], None] = _noop,
        reload: Callable[[], None] = _noop,
    ) -> None:
        self.kv = kv
        self.media_root = Path(media_root)
        self._commit = commit
        self._reload = reload

    # ------------------------------------------------------------------ media

    def media_path(self, rel: str) -> Path:
        p = (self.media_root / rel).resolve()
        if self.media_root.resolve() not in p.parents:
            raise ValueError("media path escapes the media root")
        return p

    def put_media(self, rel: str, data: bytes, commit: bool = True) -> str:
        p = self.media_path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if commit:
            self._commit()
        return rel

    def commit_media(self) -> None:
        self._commit()

    def read_media(self, rel: str) -> bytes | None:
        p = self.media_path(rel)
        if not p.exists():
            self._reload()
        return p.read_bytes() if p.exists() else None

    # ------------------------------------------------------------------ procedures

    def save_procedure(self, proc: Procedure) -> Procedure:
        self.kv[f"procedure:{proc.id}"] = proc.model_dump(mode="json")
        index = list(self.kv.get("procedures", []))
        if proc.id not in index:
            index.append(proc.id)
            self.kv["procedures"] = index
        return proc

    def get_procedure(self, proc_id: str) -> Procedure | None:
        data = self.kv.get(f"procedure:{proc_id}")
        return Procedure.model_validate(data) if data else None

    def list_procedures(self) -> list[Procedure]:
        out = []
        for pid in self.kv.get("procedures", []):
            p = self.get_procedure(pid)
            if p:
                out.append(p)
        return out

    def delete_procedure(self, proc_id: str) -> None:
        self.kv.pop(f"procedure:{proc_id}", None)
        self.kv["procedures"] = [p for p in self.kv.get("procedures", []) if p != proc_id]

    # ------------------------------------------------------------------ runs

    def save_run(self, run: Run) -> Run:
        self.kv[f"run:{run.id}"] = run.model_dump(mode="json")
        index = list(self.kv.get("runs", []))
        if run.id not in index:
            index.append(run.id)
            self.kv["runs"] = index
        return run

    def get_run(self, run_id: str) -> Run | None:
        data = self.kv.get(f"run:{run_id}")
        return Run.model_validate(data) if data else None

    def list_runs(self) -> list[Run]:
        return [r for rid in self.kv.get("runs", []) if (r := self.get_run(rid))]

    def active_run_id(self) -> str | None:
        return self.kv.get("active_run")

    def set_active_run(self, run_id: str | None) -> None:
        self.kv["active_run"] = run_id

    # Recent phone frames for a run, kept in the Dict (not the Volume) since they
    # arrive ~1 Hz and almost all are discarded. Each: {"t", "sharpness", "jpeg"}.
    def push_frame(self, run_id: str, frame: dict, keep: int) -> None:
        key = f"frames:{run_id}"
        frames = list(self.kv.get(key, []))
        frames.append(frame)
        self.kv[key] = frames[-keep:]

    def recent_frames(self, run_id: str) -> list[dict]:
        return list(self.kv.get(f"frames:{run_id}", []))

    # Still photo hand-off: the glasses ask, the phone (polling /display) captures.
    def request_photo(self, run_id: str, request_id: str) -> None:
        self.kv[f"photo_request:{run_id}"] = request_id

    def photo_request(self, run_id: str) -> str | None:
        return self.kv.get(f"photo_request:{run_id}")

    # ------------------------------------------------------------------ captures

    def save_capture(self, cap: CaptureStatus) -> CaptureStatus:
        self.kv[f"capture:{cap.id}"] = cap.model_dump(mode="json")
        return cap

    def get_capture(self, cap_id: str) -> CaptureStatus | None:
        data = self.kv.get(f"capture:{cap_id}")
        return CaptureStatus.model_validate(data) if data else None

    def active_capture_id(self) -> str | None:
        return self.kv.get("active_capture")

    def set_active_capture(self, cap_id: str | None) -> None:
        self.kv["active_capture"] = cap_id

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.kv.get(f"setting:{key}", default)

    def set_setting(self, key: str, value: Any) -> None:
        self.kv[f"setting:{key}"] = value
