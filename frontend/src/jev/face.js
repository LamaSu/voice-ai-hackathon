/**
 * Face landmarks + gaze on the call UI.
 *
 * Runs lane C's MediaPipe tracker (src/gaze, Contract 1) on the lane D camera preview,
 * sends `user_state` to the bot (~10 Hz), and draws what it sees on a canvas over the
 * preview: the face mesh, irises, a head-pose/gaze arrow and a live readout.
 * Pixels never leave the browser — only the Contract 1 numbers are sent.
 */

import { DrawingUtils, FaceLandmarker } from "@mediapipe/tasks-vision";

import { createGazeTracker, isGazeAway } from "../gaze/index.ts";

// Served from /mediapipe (copied by scripts/fetch-mediapipe.mjs) so a flaky venue network
// can't break face tracking; falls back to the CDN defaults in src/gaze/index.ts.
const LOCAL_WASM = "/mediapipe/wasm";
const LOCAL_MODEL = "/mediapipe/face_landmarker.task";
const CALIBRATION_MS = 30_000;

// iris centres and eye corners in the 478-point Face Landmarker mesh
const L_IRIS = 468, R_IRIS = 473;
const L_EYE = [33, 133], R_EYE = [362, 263];
const NOSE_TIP = 1;

function eyeRatio(lm, iris, [a, b]) {
  const dx = lm[b].x - lm[a].x;
  return Math.abs(dx) < 1e-6 ? 0.5 : (lm[iris].x - lm[a].x) / dx;
}

export function startFaceTracking({ video, overlay, readout, send, audio }) {
  const ctx = overlay.getContext("2d");
  const draw = new DrawingUtils(ctx);
  const startedAt = performance.now();
  let tracker = null;
  let last = { frame: null, state: null, eyes: null };
  let sent = 0;

  function onResult(result, frame) {
    const w = video.videoWidth || 640;
    const h = video.videoHeight || 480;
    if (overlay.width !== w || overlay.height !== h) {
      overlay.width = w;
      overlay.height = h;
    }
    ctx.clearRect(0, 0, w, h);
    const lm = result.faceLandmarks?.[0];
    last.frame = frame;
    if (!lm) {
      last.eyes = null;
      return;
    }
    const away = isGazeAway(frame.headYawDeg, frame.headPitchDeg);
    const tone = away ? "#f5a623" : "#37d67a";
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_TESSELATION, { color: "#ffffff22", lineWidth: 0.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_FACE_OVAL, { color: tone, lineWidth: 1.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LEFT_EYE, { color: "#79b8ff", lineWidth: 1 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_RIGHT_EYE, { color: "#79b8ff", lineWidth: 1 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LEFT_IRIS, { color: "#e5484d", lineWidth: 1.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_RIGHT_IRIS, { color: "#e5484d", lineWidth: 1.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LIPS, { color: "#a371f7", lineWidth: 1 });

    // eye-in-head gaze (iris position between eye corners, 0.5 = centred)
    const eyes = (eyeRatio(lm, L_IRIS, L_EYE) + eyeRatio(lm, R_IRIS, R_EYE)) / 2;
    last.eyes = eyes;

    // gaze arrow from the nose tip: head yaw/pitch plus the eye offset
    const yaw = frame.headYawDeg + (eyes - 0.5) * 60;
    const pitch = frame.headPitchDeg;
    const nx = lm[NOSE_TIP].x * w;
    const ny = lm[NOSE_TIP].y * h;
    const len = Math.min(w, h) * 0.35;
    const ex = nx - Math.sin((yaw * Math.PI) / 180) * len;
    const ey = ny - Math.sin((pitch * Math.PI) / 180) * len;
    ctx.strokeStyle = tone;
    ctx.fillStyle = tone;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(nx, ny);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ex, ey, 6, 0, Math.PI * 2);
    ctx.fill();
  }

  function onUserState(state) {
    last.state = state;
    try {
      if (send(state)) sent += 1;
    } catch (e) {
      console.warn("user_state send failed", e);
    }
    render();
  }

  function row(label, value, cls = "") {
    return `<div class="fr ${cls}"><span>${label}</span><b>${value}</b></div>`;
  }

  function render() {
    const f = last.frame;
    const s = last.state;
    const calib = performance.now() - startedAt;
    const face = f?.facePresent;
    const top = f
      ? Object.entries(f.blendshapes).sort((a, b) => b[1] - a[1]).slice(0, 3)
      : [];
    readout.innerHTML = [
      row("face", face ? "detected" : "not detected", face ? "ok" : "warn"),
      row("gaze", s ? (s.gaze_away ? "away" : "at agent") : "—", s && !s.gaze_away ? "ok" : "warn"),
      row("head yaw / pitch / roll", f && face ? `${f.headYawDeg.toFixed(0)}° / ${f.headPitchDeg.toFixed(0)}° / ${f.headRollDeg.toFixed(0)}°` : "—"),
      row("eyes (0.5 = centred)", last.eyes != null ? last.eyes.toFixed(2) : "—"),
      row("nod", s ? s.nod : "—"),
      row("wants turn", s ? (s.wants_turn ? "yes" : "no") : "—"),
      row("confusion_p", s ? s.confusion_p.toFixed(2) : "—"),
      row("brow_lower / lip_press Δ", s ? `${s.au.brow_lower.toFixed(2)} / ${s.au.lip_press.toFixed(2)}` : "—"),
      row(
        "prosody: pitch Δ / rate Δ / pause",
        s ? `${s.prosody_delta.pitch.toFixed(1)}Hz / ${s.prosody_delta.rate.toFixed(2)} / ${s.prosody_delta.pause_ms.toFixed(0)}ms` : "—",
      ),
      row("top blendshapes", top.length ? top.map(([k, v]) => `${k} ${v.toFixed(2)}`).join(", ") : "—"),
      row(
        "baseline",
        calib < CALIBRATION_MS ? `calibrating… ${Math.ceil((CALIBRATION_MS - calib) / 1000)}s` : "ready (values are deltas)",
      ),
      row("user_state sent", sent),
    ].join("");
  }

  async function start() {
    readout.innerHTML = row("face tracking", "loading MediaPipe…");
    const base = { video, onUserState, onResult, audio };
    try {
      tracker = await createGazeTracker({ ...base, wasmBaseUrl: LOCAL_WASM, modelAssetPath: LOCAL_MODEL });
    } catch (e) {
      console.warn("local MediaPipe assets unavailable, using CDN", e);
      tracker = await createGazeTracker(base);
    }
  }

  start().catch((e) => {
    readout.innerHTML = row("face tracking", `error: ${e?.message ?? e}`, "warn");
  });
  return { stop: () => tracker?.stop() };
}
