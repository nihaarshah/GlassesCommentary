import json

import pytest
from pydantic import ValidationError

from conftest import SEED_DIR
from guide.schemas import Procedure, Run, Step


def step(**kw):
    base = {"title": "Wire the coil", "instruction": "Connect A2 to 0V.", "check": "Blue wire A2 to 0V."}
    return Step(**{**base, **kw})


def test_title_word_limit():
    step(title="one two three four five")
    with pytest.raises(ValidationError):
        step(title="one two three four five six")


def test_instruction_word_limit():
    step(instruction=" ".join(["w"] * 12))
    with pytest.raises(ValidationError):
        step(instruction=" ".join(["w"] * 13))


def test_check_required():
    with pytest.raises(ValidationError):
        step(check="  ")


def test_procedure_needs_unique_steps():
    a = step(id="x")
    with pytest.raises(ValidationError):
        Procedure(title="t", steps=[a, step(id="x")])
    with pytest.raises(ValidationError):
        Procedure(title="t", steps=[])


def test_seed_validates():
    raw = json.loads((SEED_DIR / "din_rail_start_stop.json").read_text())
    raw.pop("_comment")
    proc = Procedure.model_validate(raw)
    assert 6 <= len(proc.steps) <= 15
    for s in proc.steps:
        assert s.reference_image and (SEED_DIR / s.reference_image).exists()


def test_run_start_and_issue_count():
    proc = Procedure(title="t", steps=[step(id="a"), step(id="b")])
    run = Run.start(proc, "")
    assert run.operator == "Operator"
    assert run.steps[0].started_at and run.steps[1].started_at is None
    assert run.issues_caught() == 0
