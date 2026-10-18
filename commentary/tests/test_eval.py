import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run_eval import Row, load_labels, score, targets  # noqa: E402


def rows(*specs):
    return [Row(file=f"{i}.jpg", step_id="s", label=l, tag=t, verdict=v) for i, (l, t, v) in enumerate(specs)]


def test_score_counts_false_passes_and_no_verdicts():
    rep = score(rows(("pass", "", "pass"), ("pass", "", "fail"), ("pass", "", "unsure"),
                     ("fail", "m", "fail"), ("fail", "m", "pass"), ("fail", "m", "error")))
    assert (rep.n, rep.correct, rep.false_pass, rep.false_fail, rep.no_verdict) == (6, 2, 1, 1, 2)
    assert rep.by_tag["m"] == {"n": 3, "caught": 1, "false_pass": 1, "passed": 0}


def test_targets():
    good = rows(*([("pass", "", "pass")] * 9 + [("pass", "", "unsure")] + [("fail", "m", "fail")] * 10))
    assert all(ok for _, ok, _ in targets(score(good), "m"))
    leaky = rows(*([("pass", "", "pass")] * 10 + [("fail", "m", "fail")] * 9 + [("fail", "m", "pass")]))
    res = {name: ok for name, ok, _ in targets(score(leaky), "m")}
    assert res["m caught >= 90%"] and not res["m false passes == 0"]


def test_smoke_set_labels(tmp_path):
    from eval.make_smoke_set import main

    main(tmp_path)
    rs = load_labels(tmp_path)
    assert len(rs) == 10 and {r.label for r in rs} == {"pass", "fail"}
    assert all((tmp_path / r.file).exists() for r in rs)
