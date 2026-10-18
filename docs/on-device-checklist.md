# On-glasses test checklist

Everything here needs the Ray-Ban Display glasses, the Neural Band and an
iPhone. Everything else was built and tested without them: pytest, the
deployed endpoints, xcodebuild, and the web app in a 600×600 browser window.

## Before you start

- [ ] **Anthropic credits.** The key in the `anthropic` Modal secret currently
      returns "credit balance is too low". Add credits, then check:
      `cd commentary && python -m eval.run_eval eval/data/smoke --procedure proc_seed_din_start_stop --planted missing_seal_in`
      should print real verdicts instead of `error`.
- [ ] **Deploy the web app to Vercel** from `commentary/webapp/` (same project
      as before). It now has `config.js`, `review.html` and the new screens.
- [ ] Put the Vercel URL in `GlassesCommentary/AppConfig.swift` → `webAppBase`
      so the phone can link to the review page.
- [ ] Build and run the iOS app on the phone from Xcode. Grant camera and
      microphone access when asked.
- [ ] Live commentary still works: the Commentary tab → Start Streaming, and
      the glasses web app → Live view shows captions.

## M0 spikes (report results before relying on them)

### 1. Streaming while the web app is open, plus still photos

Phone: **Camera test** tab. Glasses: web app open on the Home screen.

| Resolution | SDK size (shown in app) | Decoded size | fps over ~30 s | Notes (heat, drops) |
|---|---|---|---|---|
| low | | | | |
| medium | | | | |
| high | | | | |

- [ ] Each resolution streams while the glasses web app stays open and responsive.
- [ ] **Photo mid-stream** at each resolution: record the size, KB, and seconds,
      and whether "still streaming: true" holds afterwards.
- [ ] Watch for "Glasses are too hot" during a 5-minute stream at `high`.
- [ ] Pick the capture resolution (the highest that holds ~2 fps) and set
      `AppConfig.captureResolution` / `guideResolution`.
- [ ] Decide the Check source: if photos are clearly sharper and arrive in under
      ~2 s, redeploy with `GUIDE_CHECK_SOURCE=photo`. Otherwise keep `stream`.

### 2. Tap-to-verdict latency (target ≤ 5 s p50)

- [ ] Phone: **Guide** tab → Start guide. Glasses: pick the procedure.
- [ ] Press Check 10 times, using the band or the touchpad.
- [ ] Read the latencies:
      `curl -s $API/runs/active | python3 -c "import sys,json;r=json.load(sys.stdin);print(sorted(a['latency_ms'] for s in r['steps'] for a in s['attempts']))"`
      (server-side only). Also time tap → verdict on screen with a stopwatch
      a few times.
- [ ] p50 = ____ s, p90 = ____ s. If it's over 5 s, try `GUIDE_VERIFY_MODEL`
      and effort first, then a lower guide resolution.

### 3. Can the model read terminal labels and wire colors at arm's length?

- [ ] Take ~10 checkpoint frames of the wired panel at normal working
      distance. Use stream frames from the Guide tab
      (`modal volume get glasses-guide-media runs/<run_id>/`) and a few
      Camera-test photos.
- [ ] Put them in `commentary/eval/data/m0_frames/`, then run
      `python -m eval.run_eval eval/data/m0_frames --task read-labels`.
- [ ] Score by hand: terminal labels read correctly ____/____, wire colors ____/____.

### 4. Narration audio: glasses mic vs phone mic

- [ ] Capture tab: record ~30 s of narration with **Glasses mic**, then ~30 s
      with **Phone mic** (same words). The app shows the active input; it
      should be "Bluetooth HFP" for the glasses.
- [ ] After processing, compare `captures/<id>/transcript.json` for each
      (`modal volume get glasses-guide-media captures/<id>/transcript.json`).
- [ ] Note which is clearer at working distance with tools and noise around.

## M2: Capture (on device)

- [ ] Capture tab → title → Start capture. Glasses show **REC** with a running
      clock within ~1 s.
- [ ] Screen stays awake for the whole capture (idle timer off).
- [ ] Do the demo task once while narrating (about 3–5 min). Frame count
      climbs at about 2 per second.
- [ ] Stop & upload: the progress bar fills, then shows "Processing: …" stages,
      then "Ready for review".
- [ ] Glasses leave REC as soon as the upload finishes.
- [ ] The review page lists the draft. Steps are 6–15. Each reference photo
      shows the finished state, each clip is ≤ 5 s, and the tips are the
      expert's real words.
- [ ] Answer the follow-up questions; the answers show up as tips. Then publish.
- [ ] Fold the glasses or take them off mid-capture: the app shows a clear message.

## M3: Guide + verify (on device)

- [ ] Phone: Guide tab → operator name → Start guide. Glasses: Home lists
      published procedures.
- [ ] Band/touchpad focus moves between buttons, and the focus ring is visible.
- [ ] Step screen is readable at a glance: "N of M", title, instruction,
      reference image.
- [ ] **Check on correct work** → ✓ → advances by itself after ~1.5 s.
- [ ] **Planted mistake: leave out the 13–14 seal-in** → ⚠ with a reason that
      names the seal-in, plus the expert's photo and tip.
- [ ] Re-check after fixing → ✓.
- [ ] Shaky or blurry Check → "Hold still", not a verdict.
- [ ] Turn Wi-Fi off on the phone and press Check → "Try again" / error, never ✓.
- [ ] **Show me**: the clip plays on the glasses. If video doesn't play, the
      keyframes cycle instead, and you should tell me which happened.
- [ ] Override on a ⚠ moves on; the override is in the run (`/runs/<id>`).
- [ ] Done screen: total time, steps, issues caught (should be ≥ 1 for the
      planted mistake). It stays up until Home.
- [ ] 10-minute run at the chosen resolution: no thermal pause, phone stays
      awake, nothing gets stuck on "Checking…".

## Eval on real photos (M5 targets, needs credits)

- [ ] Take ≥ 10 photos of the seal-in step done correctly and ≥ 10 with the
      13–14 seal-in left out, from the glasses (Guide tab frames).
- [ ] Put them in `commentary/eval/data/din_rail/` with a `labels.csv`
      (see `eval/run_eval.py`). Use tag `missing_seal_in` for the mistakes.
- [ ] `python -m eval.run_eval eval/data/din_rail --procedure <published id> --planted missing_seal_in`
      Targets: missing seal-in caught ≥ 9/10, correct work passed ≥ 9/10,
      0 false passes.
