# Frontend — Lane C perception (`src/gaze/`)

`src/gaze/` is the browser-side face + prosody module for issues C1 (#8) and C3 (#10). It's wired into
the real call UI (`src/main.js`, `src/jev/face.js` — D1/#11) via `startFaceTracking`, which attaches to
the camera and mic tracks the call already opened rather than requesting them again.

## What it does

`src/gaze/index.ts` runs MediaPipe Face Landmarker on a live `<video>` element in the browser and
calls `onUserState(state)` at ~10 Hz with exactly the `user_state` shape from **Contract 1** in
`COORDINATION.md`:

```ts
{ t_ms, au: { brow_lower, lip_press }, gaze_away, nod, wants_turn, prosody_delta: { pitch, rate, pause_ms }, confusion_p }
```

Only numbers cross this boundary — no video or image data is ever passed to `onUserState`.

## Pipeline: face detection → `user_state`

```
<video> frame
  → FaceLandmarker.detectForVideo()          (MediaPipe, GPU delegate)
      → faceBlendshapes[0].categories        52 named scores in [0, 1]
      → facialTransformationMatrixes[0]      4x4 head-pose matrix
  → extractAURaw(blendshapes)                gaze/blendshapes.ts
  → BaselineTracker.addSample/.delta()       gaze/baseline.ts   (30s window)
  → matrixToEulerDeg(matrix)                 gaze/headPose.ts   → yaw/pitch/roll
  → isGazeAway(yaw, pitch)                   gaze/headPose.ts
  → NodDetector.addSample(pitch)             gaze/headPose.ts
  → estimateConfusion(auDelta, gazeAway, prosodyDelta)  gaze/confusion.ts
  → estimateWantsTurn(mouthOpenDelta, gazeAway)
  → UserState (Contract 1)                   gaze/sampler.ts

mic track (optional, C3, #10)
  → AnalyserNode.getFloatTimeDomainData()    gaze/index.ts, ~10Hz alongside the video loop
  → ProsodySampler.ingest(buffer, rate)      gaze/prosody.ts
      → estimatePitchHz()                    autocorrelation F0 estimate, null when unvoiced
      → SyllableRateEstimator                envelope-peak rate proxy
      → PauseTracker                         ms since energy last crossed the speech threshold
  → ProsodyDelta                             folds into confusion_p above and rides in prosody_delta
```

Field-by-field, where each part of `user_state` comes from:

| `user_state` field | Source signal | How it's computed |
| --- | --- | --- |
| `t_ms` | `performance.now()` at capture time | passed straight through |
| `au.brow_lower` | Blendshapes `browDownLeft` + `browDownRight` | averaged, then baseline-deltad |
| `au.lip_press` | Blendshapes `mouthPressLeft` + `mouthPressRight` | averaged, then baseline-deltad |
| `gaze_away` | Head yaw/pitch, from the facial transformation matrix | `\|yaw\| > 20°` or `\|pitch\| > 15°`; also `true` whenever no face is detected |
| `nod` | Head pitch history over a rolling 1.5s window | counts down→up pitch reversals of ≥8° as completed nods |
| `wants_turn` | Blendshape `jawOpen` (mouth-open delta) + `gaze_away` | `true` when mouth-open delta > 0.15 **and** the user is looking at the camera |
| `prosody_delta.pitch` | Autocorrelation F0 on the mic track | Hz delta from a 30s voiced-frame baseline; `0` while no `audio` track is wired in |
| `prosody_delta.rate` | Amplitude-envelope peaks/sec (syllable-rate proxy) | delta from a 30s baseline |
| `prosody_delta.pause_ms` | ms since energy last crossed the speech threshold | raw duration, not a delta (matches Contract 1's own example value) |
| `confusion_p` | `au.brow_lower`, `au.lip_press`, `gaze_away`, `prosody_delta` | `clamp01(0.7·face + 0.3·hesitation)`, face = `clamp01(0.6·brow_lower + 0.4·lip_press − 0.15 if gaze_away)`, hesitation = `0.6·clamp01(pause_ms/1500) + 0.4·clamp01(-rate)` — still a prior, not a verdict |

The **baseline** (Contract 1: "deltas from the user's own baseline, captured in the first 30 seconds")
is a running mean over each raw AU signal for the first 30s of samples, then frozen. Before it locks,
deltas are computed against the running mean so far rather than raw values, so `user_state` is always
valid, just noisier during calibration.

Thresholds above (20°/15° for gaze-away, 8° for nod swings, 0.15 for wants-turn, the 0.6/0.4/0.15
weights for confusion) are starting points picked without ground-truth data — tune them in
`gaze/headPose.ts` and `gaze/confusion.ts` once someone can validate against a real face.

**C3 (#10) decision: derived-from-audio, not Hume.** Pitch/rate/pause are estimated on-device from
the mic track the call already opened (`gaze/prosody.ts`) rather than calling a cloud emotion-from-voice
API. No new vendor, no new key, and no extra network hop on the speech path — see the decision log in
COORDINATION.md. `prosody_delta` is `{0, 0, 0}` only when no `audio` track was passed to
`createGazeTracker` (e.g. the mic hasn't connected yet); it isn't a stub otherwise.

Known stubs and heuristics, left for other issues rather than guessed at here:
- `confusion_p` fuses face (70%) and prosody hesitation (30%) into a single prior per the decision log
  in COORDINATION.md — not a verdict; Lane B (#5) owns turning this into an actual confusion hypothesis.
- The rate proxy (envelope-peak rate) stands in for real speaking rate — a true words/sec measure needs
  word boundaries this module doesn't have without running ASR client-side too.
- The 30s baseline/delta math lives here (Contract 1 requires it to emit valid deltas at all), but
  calibration UX (a visible countdown, recalibrate trigger, robustness) is C2 (#9).

## Run the call UI

```bash
npm install
npm run dev
```

Open the printed localhost URL, connect, and allow camera + mic access. The face readout panel shows
live `gaze`/`confusion_p`/`prosody` values next to the video. The first 30 seconds establish your
baseline (deltas ramp against a running mean until then); after that the numbers are deltas from your
calibrated baseline.

## Tests

```bash
npm test
```

Covers the pure logic (blendshape mapping, baseline delta math, head-pose extraction, nod detection,
confusion heuristic, and the sampler's throttling + output shape) without needing a camera. The
MediaPipe/webcam wiring in `src/gaze/index.ts` and the demo page are not covered by these tests —
they need a real browser with camera access to verify end to end.

## Integrating into a call UI

```ts
import { createGazeTracker } from "./gaze";

const tracker = await createGazeTracker({
  video: videoElement, // must already have a live camera stream attached
  audio: micTrack,     // optional (C3): same MediaStreamTrack/MediaStream the call already opened
  onUserState: (state) => sendOverYourTransport(state),
});
// later: tracker.stop();
```

Omitting `audio` (or passing `null`) keeps the pre-C3 face-only behavior — nothing else changes.
`src/main.js` does exactly this: it captures the local mic `MediaStreamTrack` from
`RTVIEvent.TrackStarted` (never playing it back) and passes it straight through.

How `user_state` actually reaches the reasoner (RTVI data channel, WebSocket, etc.) is a transport
choice for whoever wires Lane C into Lane A/B — not fixed by Contract 1, which only pins the JSON
shape.
