You're working in my repo GlassesCommentary. Read the whole repo before changing anything.
Context: this is for a 60–90 second demo video due in about two weeks. Favor simple and reliable over clever.

## Where it stands
- GlassesCommentary/ (iOS, SwiftUI): registers with the Meta AI app via the Meta Wearables Device Access Toolkit (DAT iOS, pinned 0.9.0), streams Ray-Ban Display camera frames (.hvc1, .low, 2 fps), decodes them with VideoFrameDecoder, and every 4 s POSTs a JPEG to Modal (GlassesCommentaryStream.swift).
- commentary/modal_app.py: Moondream2 on a T4. `caption` (POST) answers a prompt about a frame and writes it to a modal.Dict; `latest` (GET, CORS) returns it.
- commentary/webapp/: a static Ray-Ban Display Web App on Vercel (600×600, black = transparent, .focusable buttons driven by the Neural Band/touchpad). It polls `latest`. Web apps have no camera access, so the phone is always the camera and the glasses web app is display + input.
- commentary/client.py: webcam test client.
Keep this split (phone = camera, glasses web app = display/input, Modal = state + AI). Keep live commentary working.

## Goal
One loop, end to end: an expert does a hands-on task once while talking → the system turns it into a step-by-step guide → a novice is guided through it on the glasses → each step is checked against the expert's reference photo → a planted mistake is caught → a work record is produced.
Demo task: wiring a 24V DC start/stop circuit on a DIN rail. +24V → Stop (NC) → Start (NO) → contactor coil A1; A2 → 0V; pilot light across A1–A2; seal-in (holding) contact 13–14 wired in parallel with Start. Planted mistake: the novice leaves out the 13–14 seal-in wiring.
Everything must stay task-agnostic. No logic specific to this panel outside the seed data.

## What to build, in order

M0: Spikes. Report results before building on them.
1. Can the phone stream the camera while the glasses web app is open on the display? Measure resolution/fps for each StreamingResolution. Check facebook/meta-wearables-dat-ios samples/CameraAccess for a still-photo API and whether it works mid-stream.
2. Latency from a button tap on the glasses to a verdict on the display. Target ≤5 s p50.
3. Can a strong vision model read terminal labels (A1, A2, 13, 14) and wire colors from a checkpoint frame taken at arm's length?
4. Narration audio: the glasses' Bluetooth mic via AVAudioSession vs. the phone mic.

M1: Data model + backend
- Procedure JSON schema: steps with title (≤5 words), instruction (≤12 words), tips (the expert's own words), tools/parts, start/end time in the source recording, reference image, optional clip, and `check`: what must be visibly true when the step is done, in concrete visual terms (wire color + terminal label).
- Run JSON schema: operator, timestamps per step, every check attempt (image, verdict pass/fail/unsure, reason), overrides. One active run at a time is fine.
- New endpoints in one FastAPI app on Modal (@modal.asgi_app) with CORS middleware, since the vercel.app web app will POST JSON. Media in a modal.Volume, state in a modal.Dict. Leave `caption`/`latest` untouched.
- A seed procedure for the demo task, so M3 can start before M2 is done.

M2: Capture
- iOS Capture mode: stream at the highest resolution that holds ~2 fps. Save JPEG frames with timestamps plus narration audio locally, then upload one bundle at the end with progress. Disable the idle timer during sessions.
- Glasses web app: a Recording screen (red dot, elapsed time).
- Modal pipeline (spawned job + status endpoint):
  - Transcribe with word timestamps (Whisper on GPU).
  - Split into 6–15 steps using the transcript plus sampled frames.
  - Per step: use the sharpest frame near the step's end as the reference; write the instruction, tips (verbatim quotes), tools/parts and check; render a ≤5 s clip.
  - Generate 2–3 follow-up questions about moments the narration didn't explain (e.g. "why did you tug every wire?").
- A simple static review page in the webapp folder, for phone or laptop browsers: edit, reorder, merge or delete steps; answer the follow-up questions (answers become tips); publish.
- Use a strong hosted multimodal model via API for step splitting, writing and verification, behind a small interface. Provider/model go in config, keys in Modal secrets. Moondream stays for commentary.

M3: Guide + verify
- Glasses web app screens:
  - Home: pick a procedure.
  - Step: "N of M", title, instruction, reference image; buttons Check · Show me · Back.
  - Result: ✓ auto-advances after ~1.5 s. ⚠ shows the reason, the expert's reference image and tip, with buttons Re-check · Override.
  - Done: total time, steps, issues caught, link to the record.
  - Big type (≥24px), few words, high contrast. Poll run state ~1 Hz or use a WebSocket.
- Phone Guide mode: upload a frame ~1 Hz to the run. On Check, the backend uses the sharpest of the last few frames (or the still-photo API, if M0 shows it's better).
- Verifier:
  - Input: the expert's reference image, the current frame and the step's check text. Temperature 0. Strict JSON {verdict, reason ≤12 words}.
  - Blurry frame → "hold still" instead of a verdict. "unsure" → tell the user what to look at more closely.
  - Errors or timeouts never auto-pass.
- Show me: play the clip if web-app video works on the device; otherwise cycle 3–4 key frames.

M4: Record + test support
- Work record per run: HTML that prints cleanly to PDF. Procedure, operator, start/end, and each step's photo, verdict, reason, time and overrides.
- A printable version of the same steps for a control group.
- CSV export of runs (time per step, failed checks, overrides) for a 3-vs-3 user test.

M5: Eval + polish
- eval/: a script that runs the verifier over a folder of labeled checkpoint photos per step and reports accuracy, false passes and false fails. Targets on the demo task: catch the missing seal-in wiring in ≥9/10 photos, pass correct work in ≥9/10, zero false passes on the planted mistake.
- README: setup, deploy (Modal, Vercel, Xcode), config, a demo runbook (capture → review → guide → record), and an on-device test checklist.

## Constraints
- Verify every DAT API against the official iOS samples/docs before using it (the header comment in GlassesCommentaryStream.swift explains why). Stay on DAT 0.9.0 unless something needs 1.x; ask me first.
- Display: 600×600, full redraws, black = transparent. Keep images ≤600 px and small (it's Bluetooth). For video, assume ≤400 px per side and ≤70,000 total pixels unless the Web Apps docs say otherwise. Watch DeviceState/thermal; streaming pauses when hot.
- Follow the existing Swift patterns (@Observable, @MainActor, ListenerTokenBag, typed throws). Keep the web app static vanilla JS with no build step, matching app.js.
- Move the hardcoded endpoint URLs (ContentView.swift, webapp/app.js) into one config spot per client. No secrets in the repo.
- Never fake results in the demo path: no hardcoded verdicts or canned steps outside the seed file.
- Out of scope: haptic band, robotics, auth/multi-tenant, ServiceNow/SAP integrations, continuous video verification, App Store publishing.

## How to work
1. Read the repo and the DAT samples, then send me a plan: files to add/change, endpoints, schemas, and the M0 test steps. Wait for my OK.
2. Build one milestone at a time with small commits. Compile with xcodebuild if available. Add Python tests for the pipeline pieces (schema validation, frame selection, clip limits, verifier JSON parsing).
3. After each milestone, give me a short on-device test checklist and stop so I can try it on the glasses.
4. If the SDK or hardware can't do something in this spec, tell me and propose the closest alternative. Don't work around it silently.
