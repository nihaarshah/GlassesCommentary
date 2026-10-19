"""Work record for a run: one self-contained HTML page that prints cleanly to PDF.

Procedure, operator, start/end, and for each step: the expert reference, every
check attempt (photo, verdict, reason, time), overrides and time on step.
Images are linked from /media on the same API origin.
"""

from __future__ import annotations

import datetime as dt
from html import escape

from .schemas import Procedure, Run

VERDICT_LABEL = {
    "pass": ("Passed", "pass"),
    "fail": ("Issue caught", "fail"),
    "unsure": ("Unsure", "warn"),
    "hold_still": ("Too blurry", "warn"),
    "error": ("Check error", "warn"),
}


# Times are rendered in the viewer's time zone by a small script at the end of
# the page (the server runs in UTC); the UTC text is the no-JS fallback.
def _ts(t: float | None) -> str:
    if not t:
        return "—"
    utc = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f'<time data-t="{t}" data-f="date">{utc}</time>'


def _clock(t: float | None) -> str:
    if not t:
        return "—"
    utc = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S UTC")
    return f'<time data-t="{t}" data-f="time">{utc}</time>'


def _dur(a: float | None, b: float | None) -> str:
    if not a or not b:
        return "—"
    s = max(0, int(round(b - a)))
    return f"{s // 60}:{s % 60:02d}"


def step_outcome(rs) -> tuple[str, str]:
    """(label, css class) summarizing a step for the table."""
    if rs.override:
        return "Overridden", "warn"
    if any(a.verdict == "pass" for a in rs.attempts):
        fails = sum(1 for a in rs.attempts if a.verdict == "fail")
        return ("Passed after fix" if fails else "Passed"), "pass"
    if rs.ended_at:
        return "Completed, not verified", "warn"
    return "Not reached", "muted"


def render_record(run: Run, proc: Procedure | None, media_base: str = "/media") -> str:
    steps_by_id = {s.id: s for s in (proc.steps if proc else [])}
    passed = sum(1 for rs in run.steps if any(a.verdict == "pass" for a in rs.attempts))
    overrides = sum(1 for rs in run.steps if rs.override)
    attempts = sum(len(rs.attempts) for rs in run.steps)
    status = "Completed" if run.ended_at else "In progress"

    def img(path: str | None, cls: str = "") -> str:
        return f'<img class="{cls}" src="{escape(media_base)}/{escape(path)}" alt="">' if path else '<div class="noimg">no photo</div>'

    rows, details = [], []
    for i, rs in enumerate(run.steps, 1):
        step = steps_by_id.get(rs.step_id)
        title = step.title if step else rs.step_id
        label, cls = step_outcome(rs)
        rows.append(
            f"<tr><td>{i}</td><td>{escape(title)}</td><td><span class='tag {cls}'>{label}</span></td>"
            f"<td>{len(rs.attempts)}</td><td>{_dur(rs.started_at, rs.ended_at)}</td></tr>"
        )
        att_html = []
        for a in rs.attempts:
            vlabel, vcls = VERDICT_LABEL.get(a.verdict, (a.verdict, "warn"))
            att_html.append(
                "<div class='attempt'>"
                f"{img(a.image, 'shot')}"
                f"<div><span class='tag {vcls}'>{vlabel}</span> <span class='muted'>{_clock(a.at)}"
                f"{f' · {a.latency_ms / 1000:.1f} s' if a.latency_ms else ''}</span>"
                f"<p>{escape(a.reason)}</p></div></div>"
            )
        if rs.override:
            att_html.append(
                f"<div class='override'><b>Override</b> at {_clock(rs.override.at)}: {escape(rs.override.note)}</div>"
            )
        details.append(
            "<section class='step'>"
            f"<h3>{i}. {escape(title)} <span class='tag {cls}'>{label}</span></h3>"
            f"<p class='muted'>{escape(step.instruction) if step else ''}</p>"
            f"<p><b>Check:</b> {escape(step.check) if step else '—'}</p>"
            "<div class='cols'>"
            f"<figure>{img(step.reference_image if step else None, 'ref')}<figcaption>Expert reference</figcaption></figure>"
            f"<div class='attempts'>{''.join(att_html) or '<p class=muted>No checks recorded.</p>'}</div>"
            "</div></section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work record · {escape(run.procedure_title)}</title>
<style>
  :root {{ --ink:#1b1b1b; --muted:#6b6b6b; --line:#ddd; --pass:#1e7d32; --fail:#c62828; --warn:#b26a00; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0 auto; max-width: 960px; padding: 24px 16px 48px; color: var(--ink); background: #fff;
         font: 14px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }} h2 {{ font-size: 18px; margin: 28px 0 8px; }}
  h3 {{ font-size: 16px; margin: 0 0 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .muted {{ color: var(--muted); }}
  .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px 16px; margin: 16px 0; }}
  .meta div {{ border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }}
  .meta b {{ display: block; font-size: 18px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  .tag {{ display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid currentColor; }}
  .pass {{ color: var(--pass); }} .fail {{ color: var(--fail); }} .warn {{ color: var(--warn); }}
  .step {{ border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin: 12px 0; break-inside: avoid; }}
  .cols {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  figure {{ margin: 0; width: 200px; }} figcaption {{ font-size: 12px; color: var(--muted); }}
  .ref, .shot {{ width: 200px; max-height: 260px; object-fit: contain; background: #f2f2f2; border-radius: 6px; }}
  .shot {{ width: 140px; max-height: 180px; }}
  .attempts {{ flex: 1; min-width: 260px; display: flex; flex-direction: column; gap: 8px; }}
  .attempt {{ display: flex; gap: 10px; align-items: flex-start; }} .attempt p {{ margin: 4px 0 0; }}
  .override {{ border-left: 3px solid var(--warn); padding-left: 8px; }}
  .noimg {{ width: 140px; height: 90px; display: grid; place-items: center; background: #f2f2f2; color: var(--muted); border-radius: 6px; font-size: 12px; }}
  .print {{ float: right; }}
  @media print {{ .print {{ display: none; }} body {{ padding: 0; }} a {{ color: inherit; }} }}
</style></head><body>
<button class="print" onclick="window.print()">Print / Save PDF</button>
<h1>Work record</h1>
<div class="muted">{escape(run.procedure_title)} · run {escape(run.id)}</div>
<div class="meta">
  <div>Operator<b>{escape(run.operator)}</b></div>
  <div>Status<b>{status}</b></div>
  <div>Started<b>{_clock(run.started_at)}</b><span class="muted">{_ts(run.started_at)}</span></div>
  <div>Finished<b>{_clock(run.ended_at)}</b></div>
  <div>Total time<b>{_dur(run.started_at, run.ended_at)}</b></div>
  <div>Steps verified<b>{passed} / {len(run.steps)}</b></div>
  <div>Issues caught<b class="{'fail' if run.issues_caught() else ''}">{run.issues_caught()}</b></div>
  <div>Overrides<b class="{'warn' if overrides else ''}">{overrides}</b></div>
  <div>Photo checks<b>{attempts}</b></div>
</div>
<h2>Summary</h2>
<table><thead><tr><th>#</th><th>Step</th><th>Outcome</th><th>Checks</th><th>Time</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Step detail</h2>
{''.join(details)}
<p class="muted">Generated {_ts(dt.datetime.now().timestamp())}. Verdicts are from automated photo checks against the expert's reference; overrides are operator decisions.</p>
<script>
  document.querySelectorAll("time[data-t]").forEach(function (el) {{
    var d = new Date(parseFloat(el.dataset.t) * 1000);
    el.textContent = el.dataset.f === "date"
      ? d.toLocaleDateString(undefined, {{ year: "numeric", month: "short", day: "numeric" }})
      : d.toLocaleTimeString();
  }});
</script>
</body></html>"""
