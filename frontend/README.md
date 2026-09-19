# Frontend — Lane C perception (`src/gaze/`)

This currently ships only `src/gaze/`, the browser-side face-feature module for issue C1 (#8), plus a
minimal demo harness (`index.html`, `src/main.ts`) to eyeball it. The call UI / latency HUD shell
(D1, #11) is expected to own everything else under `frontend/` and can import from `src/gaze` directly.

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
  → estimateConfusion(auDelta, gazeAway)     gaze/confusion.ts
  → estimateWantsTurn(mouthOpenDelta, gazeAway)
  → UserState (Contract 1)                   gaze/sampler.ts
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
| `prosody_delta.*` | — (audio, not video) | always `{0, 0, 0}`; stub for C3 (#10) |
| `confusion_p` | `au.brow_lower`, `au.lip_press`, `gaze_away` | `clamp01(0.6·brow_lower + 0.4·lip_press − 0.15 if gaze_away)` — a face-only prior, not a verdict |

The **baseline** (Contract 1: "deltas from the user's own baseline, captured in the first 30 seconds")
is a running mean over each raw AU signal for the first 30s of samples, then frozen. Before it locks,
deltas are computed against the running mean so far rather than raw values, so `user_state` is always
valid, just noisier during calibration.

Thresholds above (20°/15° for gaze-away, 8° for nod swings, 0.15 for wants-turn, the 0.6/0.4/0.15
weights for confusion) are starting points picked without ground-truth data — tune them in
`gaze/headPose.ts` and `gaze/confusion.ts` once someone can validate against a real face.

Known stubs, left for other issues rather than guessed at here:
- `prosody_delta` is always zero. It's audio-derived and belongs to C3 (#10, optional prosody fusion).
- `confusion_p` is a face-only heuristic (brow lower + lip press, penalized when looking away). It's
  meant as a rough prior per the decision log in COORDINATION.md, not a verdict; Lane B (#5) owns
  turning this into an actual confusion hypothesis.
- The 30s baseline/delta math lives here (Contract 1 requires it to emit valid deltas at all), but
  calibration UX (a visible countdown, recalibrate trigger, robustness) is C2 (#9).

## Run the demo harness

```bash
npm install
npm run dev
```

Open the printed localhost URL, allow camera access, and watch the JSON panel. The first 30 seconds
establish your baseline (deltas ramp against a running mean until then); after that the numbers are
deltas from your calibrated baseline.

## Tests

```bash
npm test
```

Covers the pure logic (blendshape mapping, baseline delta math, head-pose extraction, nod detection,
confusion heuristic, and the sampler's throttling + output shape) without needing a camera. The
MediaPipe/webcam wiring in `src/gaze/index.ts` and the demo page are not covered by these tests —
they need a real browser with camera access to verify end to end.

## Integrating into the real call UI

```ts
import { createGazeTracker } from "./gaze";

const tracker = await createGazeTracker({
  video: videoElement, // must already have a live camera stream attached
  onUserState: (state) => sendOverYourTransport(state),
});
// later: tracker.stop();
```

How `user_state` actually reaches the reasoner (RTVI data channel, WebSocket, etc.) is a transport
choice for whoever wires Lane C into Lane A/B — not fixed by Contract 1, which only pins the JSON
shape.
