"""Run the production verifier over labeled checkpoint photos and report accuracy.

Data layout (one folder per procedure):

  eval/data/<name>/
    labels.csv          file,step_id,label,tag
                        file: path relative to this folder
                        label: pass | fail      (ground truth for the step's check)
                        tag: optional, e.g. missing_seal_in (a planted mistake)

The procedure (check text + expert reference image per step) comes from the
deployed API. Verification runs on Modal (`verify_remote`) so it uses the same
code, model and secret as production.

  cd commentary
  python -m eval.run_eval eval/data/din_rail --procedure proc_seed_din_start_stop
  python -m eval.run_eval eval/data/din_rail --procedure ... --planted missing_seal_in
  python -m eval.run_eval eval/data/m0_frames --task read-labels      # M0 spike 3

Targets for the demo task: catch the planted mistake in >= 9/10 photos, pass
correct work in >= 9/10, zero false passes on the planted mistake.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

API = "https://nihaarshah--glasses-guide-api.us-east.modal.run"


@dataclass
class Row:
    file: str
    step_id: str
    label: str  # pass | fail
    tag: str = ""
    verdict: str = ""
    reason: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    n: int = 0
    correct: int = 0
    false_pass: int = 0  # label fail, verdict pass  (the dangerous one)
    false_fail: int = 0  # label pass, verdict fail
    no_verdict: int = 0  # unsure / hold_still / error
    pass_total: int = 0
    pass_ok: int = 0
    fail_total: int = 0
    fail_caught: int = 0
    by_tag: dict = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


def load_labels(folder: Path) -> list[Row]:
    with open(folder / "labels.csv", newline="") as f:
        rows = [Row(file=r["file"], step_id=r["step_id"], label=r["label"].strip().lower(), tag=(r.get("tag") or "").strip()) for r in csv.DictReader(f)]
    bad = [r for r in rows if r.label not in ("pass", "fail")]
    if bad:
        raise SystemExit(f"labels must be pass|fail: {[r.file for r in bad]}")
    return rows


def score(rows: list[Row]) -> Report:
    """Pure: verdicts vs labels. 'unsure'/'hold_still'/'error' are never correct,
    and never count as a pass."""
    rep = Report()
    for r in rows:
        rep.n += 1
        t = rep.by_tag.setdefault(r.tag or "-", {"n": 0, "caught": 0, "false_pass": 0, "passed": 0})
        t["n"] += 1
        if r.label == "pass":
            rep.pass_total += 1
            if r.verdict == "pass":
                rep.pass_ok += 1
                rep.correct += 1
                t["passed"] += 1
            elif r.verdict == "fail":
                rep.false_fail += 1
            else:
                rep.no_verdict += 1
        else:
            rep.fail_total += 1
            if r.verdict == "fail":
                rep.fail_caught += 1
                rep.correct += 1
                t["caught"] += 1
            elif r.verdict == "pass":
                rep.false_pass += 1
                t["false_pass"] += 1
            else:
                rep.no_verdict += 1
    return rep


def targets(rep: Report, planted: str | None) -> list[tuple[str, bool, str]]:
    out = []
    if rep.pass_total:
        out.append(("correct work passed >= 90%", rep.pass_ok / rep.pass_total >= 0.9, f"{rep.pass_ok}/{rep.pass_total}"))
    if planted:
        t = rep.by_tag.get(planted, {"n": 0, "caught": 0, "false_pass": 0})
        out.append((f"{planted} caught >= 90%", t["n"] > 0 and t["caught"] / t["n"] >= 0.9, f"{t['caught']}/{t['n']}"))
        out.append((f"{planted} false passes == 0", t["false_pass"] == 0, str(t["false_pass"])))
    return out


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def run_verify(folder: Path, procedure_id: str, api: str, limit: int | None) -> list[Row]:
    import modal

    verify = modal.Function.from_name("glasses-guide", "verify_remote")
    proc = json.loads(fetch(f"{api}/procedures/{procedure_id}"))
    steps = {s["id"]: s for s in proc["steps"]}
    refs: dict[str, bytes | None] = {}
    rows = load_labels(folder)[:limit]
    for i, r in enumerate(rows, 1):
        step = steps.get(r.step_id)
        if not step:
            raise SystemExit(f"{r.file}: step {r.step_id} not in {procedure_id}")
        if r.step_id not in refs:
            refs[r.step_id] = fetch(f"{api}/media/{step['reference_image']}") if step.get("reference_image") else None
        t0 = time.time()
        out = verify.remote(refs[r.step_id], (folder / r.file).read_bytes(), step["check"])
        r.verdict, r.reason, r.seconds = out["verdict"], out["reason"], round(time.time() - t0, 2)
        mark = "ok " if (r.verdict == r.label) else "XX "
        print(f"{mark}[{i}/{len(rows)}] {r.file:<40} label={r.label:<4} verdict={r.verdict:<10} {r.seconds:>5.1f}s  {r.reason}", flush=True)
    return rows


def run_read_labels(folder: Path) -> None:
    import modal

    read = modal.Function.from_name("glasses-guide", "read_labels_remote")
    for p in sorted(folder.glob("*.jp*g")):
        out = read.remote(p.read_bytes())
        print(f"\n== {p.name}")
        print("terminals:", ", ".join(out["terminals"]) or "-")
        for w in out["wires"]:
            print(f"  {w['color']:<8} {w['from']} -> {w['to']}")
        if out["unreadable"]:
            print("unreadable:", out["unreadable"])
    print("\nScore by hand against the photos (M0 spike 3).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", type=Path)
    p.add_argument("--procedure", help="procedure id on the API (for --task verify)")
    p.add_argument("--api", default=API)
    p.add_argument("--task", choices=["verify", "read-labels"], default="verify")
    p.add_argument("--planted", help="tag of the planted mistake, e.g. missing_seal_in")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", type=Path, help="write a JSON report here")
    a = p.parse_args()

    if a.task == "read-labels":
        run_read_labels(a.folder)
        return
    if not a.procedure:
        raise SystemExit("--procedure is required for --task verify")
    rows = run_verify(a.folder, a.procedure, a.api.rstrip("/"), a.limit)
    rep = score(rows)
    print(f"\naccuracy {rep.accuracy:.0%} ({rep.correct}/{rep.n})  false passes {rep.false_pass}  false fails {rep.false_fail}  no verdict {rep.no_verdict}")
    lat = sorted(r.seconds for r in rows)
    if lat:
        print(f"latency p50 {lat[len(lat) // 2]:.1f}s  max {lat[-1]:.1f}s (Modal call, excludes phone upload)")
    ok_all = True
    for name, ok, detail in targets(rep, a.planted):
        ok_all &= ok
        print(f"  [{'PASS' if ok else 'MISS'}] {name}: {detail}")
    if a.out:
        a.out.write_text(json.dumps({"report": asdict(rep), "rows": [asdict(r) for r in rows]}, indent=2))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
