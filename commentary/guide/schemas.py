"""Procedure and Run schemas.

Everything here is task-agnostic: a procedure is just ordered steps, each with
a concrete visual `check`. Panel-specific content lives only in seed/*.json.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TITLE_MAX_WORDS = 5
INSTRUCTION_MAX_WORDS = 12
REASON_MAX_WORDS = 12


def word_count(text: str) -> int:
    return len(text.split())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> float:
    return time.time()


def _limit_words(value: str, limit: int, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    if word_count(value) > limit:
        raise ValueError(f"{field} must be at most {limit} words (got {word_count(value)})")
    return value


# --------------------------------------------------------------------------- procedure


class Step(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    instruction: str
    tips: list[str] = Field(default_factory=list, description="The expert's own words, verbatim")
    tools: list[str] = Field(default_factory=list)
    parts: list[str] = Field(default_factory=list)
    t_start: float | None = Field(default=None, description="Seconds into the source recording")
    t_end: float | None = None
    reference_image: str | None = Field(default=None, description="Media path, served at /media/<path>")
    clip: str | None = None
    keyframes: list[str] = Field(default_factory=list, description="Fallback for Show me if video can't play")
    check: str = Field(description="What must be visibly true when done: wire color + terminal label")

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _limit_words(v, TITLE_MAX_WORDS, "title")

    @field_validator("instruction")
    @classmethod
    def _instruction(cls, v: str) -> str:
        return _limit_words(v, INSTRUCTION_MAX_WORDS, "instruction")

    @field_validator("check")
    @classmethod
    def _check(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("check must not be empty")
        return v


class FollowUp(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fq"))
    question: str
    t: float | None = Field(default=None, description="Moment in the recording it refers to")
    step_id: str | None = None
    answer: str | None = None


class Procedure(BaseModel):
    id: str = Field(default_factory=lambda: new_id("proc"))
    title: str
    status: Literal["draft", "published"] = "draft"
    source_capture: str | None = None
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)
    steps: list[Step]
    followups: list[FollowUp] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _steps(cls, v: list[Step]) -> list[Step]:
        if not v:
            raise ValueError("a procedure needs at least one step")
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return v

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status, "steps": len(self.steps)}


# --------------------------------------------------------------------------- run

Verdict = Literal["pass", "fail", "unsure", "hold_still", "error"]


class CheckAttempt(BaseModel):
    at: float = Field(default_factory=now)
    image: str | None = None
    verdict: Verdict
    reason: str
    source: Literal["stream", "photo"] = "stream"
    latency_ms: int | None = None


class Override(BaseModel):
    at: float = Field(default_factory=now)
    note: str = "Operator override"


class RunStep(BaseModel):
    step_id: str
    started_at: float | None = None
    ended_at: float | None = None
    attempts: list[CheckAttempt] = Field(default_factory=list)
    override: Override | None = None

    @property
    def passed(self) -> bool:
        return any(a.verdict == "pass" for a in self.attempts)


Screen = Literal["step", "checking", "result", "done"]


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    procedure_id: str
    procedure_title: str = ""
    operator: str = "Operator"
    started_at: float = Field(default_factory=now)
    ended_at: float | None = None
    current_step: int = 0
    screen: Screen = "step"
    steps: list[RunStep]

    @classmethod
    def start(cls, procedure: Procedure, operator: str) -> "Run":
        t = now()
        steps = [RunStep(step_id=s.id) for s in procedure.steps]
        steps[0].started_at = t
        return cls(
            procedure_id=procedure.id,
            procedure_title=procedure.title,
            operator=operator or "Operator",
            started_at=t,
            steps=steps,
        )

    @property
    def last_attempt(self) -> CheckAttempt | None:
        if self.screen == "done":
            return None
        attempts = self.steps[self.current_step].attempts
        return attempts[-1] if attempts else None

    def issues_caught(self) -> int:
        """Steps where at least one check failed (a mistake the system flagged)."""
        return sum(1 for s in self.steps if any(a.verdict == "fail" for a in s.attempts))


# --------------------------------------------------------------------------- capture

CaptureState = Literal["recording", "uploaded", "processing", "done", "error"]


class CaptureStatus(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cap"))
    state: CaptureState = "recording"
    started_at: float = Field(default_factory=now)
    title: str = "Untitled procedure"
    progress: float = 0.0
    stage: str = ""
    error: str | None = None
    procedure_id: str | None = None
