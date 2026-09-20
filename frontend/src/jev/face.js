/**
 * Face landmarks + gaze on the call UI, for everyone in frame.
 *
 * Runs lane C's MediaPipe tracker (src/gaze, Contract 1) on the lane D camera preview:
 * - draws the mesh, irises, lips and a gaze arrow for EVERY detected face
 * - sends `user_state` (Contract 1, one person: the largest face) to the bot
 * - sends `faces` (lane A) with per-face gaze, so Jev can tell "someone is talking to me"
 *   from "two people are talking to each other"
 *
 * Pixels never leave the browser; only these numbers are sent.
 */

import { DrawingUtils, FaceLandmarker } from "@mediapipe/tasks-vision";

import { createGazeTracker, isGazeAway } from "../gaze/index.ts";
import { matrixToEulerDeg } from "../gaze/headPose.ts";

// Served from /mediapipe (copied by scripts/fetch-mediapipe.mjs) so a flaky venue network
// can't break face tracking; falls back to the CDN defaults in src/gaze/index.ts.
const LOCAL_WASM = "/mediapipe/wasm";
const LOCAL_MODEL = "/mediapipe/face_landmarker.task";
const CALIBRATION_MS = 30_000;
const MAX_FACES = 4;
const FACES_MSG_HZ = 5;

// iris centres and eye corners in the 478-point Face Landmarker mesh
const L_IRIS = 468, R_IRIS = 473;
const L_EYE = [33, 133], R_EYE = [362, 263];
const NOSE_TIP = 1;
const FACE_COLORS = ["#37d67a", "#79b8ff", "#f5a623", "#a371f7"];

function eyeRatio(lm, iris, [a, b]) {
  const dx = lm[b].x - lm[a].x;
  return Math.abs(dx) < 1e-6 ? 0.5 : (lm[iris].x - lm[a].x) / dx;
}

function faceArea(lm) {
  let minX = 1, maxX = 0, minY = 1, maxY = 0;
  for (const p of lm) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  return (maxX - minX) * (maxY - minY);
}

export function startFaceTracking({ video, overlay, readout, send, sendFaces, audio }) {
  const ctx = overlay.getContext("2d");
  const draw = new DrawingUtils(ctx);
  const startedAt = performance.now();
  let tracker = null;
  let faces = []; // per-face summary, recomputed every frame
  let state = null; // latest Contract 1 user_state
  let sent = 0;
  let lastFacesSent = 0;

  function summarize(result) {
    const out = [];
    const all = result.faceLandmarks ?? [];
    for (let i = 0; i < all.length; i++) {
      const lm = all[i];
      const m = result.facialTransformationMatrixes?.[i];
      const pose = m ? matrixToEulerDeg(m.data) : { yaw: 0, pitch: 0, roll: 0 };
      const eyes = (eyeRatio(lm, L_IRIS, L_EYE) + eyeRatio(lm, R_IRIS, R_EYE)) / 2;
      out.push({
        i,
        lm,
        area: faceArea(lm),
        yaw: pose.yaw,
        pitch: pose.pitch,
        roll: pose.roll,
        eyes,
        looking_at_agent: !isGazeAway(pose.yaw, pose.pitch),
      });
    }
    // the largest face is the one Contract 1 describes (lane C's sampler uses face 0,
    // which MediaPipe orders by detection, so we only rely on this for our own message)
    if (out.length) {
      const primary = out.reduce((a, b) => (b.area > a.area ? b : a));
      primary.primary = true;
    }
    return out;
  }

  function drawFace(f, color, w, h) {
    const { lm } = f;
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_TESSELATION, { color: "#ffffff22", lineWidth: 0.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_FACE_OVAL, { color, lineWidth: f.primary ? 2 : 1.2 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LEFT_EYE, { color, lineWidth: 1 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_RIGHT_EYE, { color, lineWidth: 1 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LEFT_IRIS, { color: "#e5484d", lineWidth: 1.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_RIGHT_IRIS, { color: "#e5484d", lineWidth: 1.5 });
    draw.drawConnectors(lm, FaceLandmarker.FACE_LANDMARKS_LIPS, { color: "#a371f7", lineWidth: 1 });

    // gaze arrow from the nose: head direction plus where the eyes sit in their sockets
    const yaw = f.yaw + (f.eyes - 0.5) * 60;
    const nx = lm[NOSE_TIP].x * w;
    const ny = lm[NOSE_TIP].y * h;
    const len = Math.min(w, h) * 0.3;
    const ex = nx - Math.sin((yaw * Math.PI) / 180) * len;
    const ey = ny - Math.sin((f.pitch * Math.PI) / 180) * len;
    ctx.strokeStyle = f.looking_at_agent ? color : "#8b949e";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = f.primary ? 3 : 2;
    ctx.beginPath();
    ctx.moveTo(nx, ny);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ex, ey, f.primary ? 6 : 4, 0, Math.PI * 2);
    ctx.fill();

    // label above the face: which person, and whether they're looking at the agent
    ctx.font = "600 14px ui-sans-serif, system-ui";
    ctx.fillStyle = color;
    const label = `face ${f.i + 1}${f.primary ? " (primary)" : ""} · ${f.looking_at_agent ? "at agent" : "away"}`;
    ctx.fillText(label, Math.max(4, lm[NOSE_TIP].x * w - 60), Math.max(16, (lm[10]?.y ?? lm[NOSE_TIP].y) * h - 10));
  }

  function onResult(result) {
    const w = video.videoWidth || 640;
    const h = video.videoHeight || 480;
    if (overlay.width !== w || overlay.height !== h) {
      overlay.width = w;
      overlay.height = h;
    }
    ctx.clearRect(0, 0, w, h);
    faces = summarize(result);
    faces.forEach((f, i) => drawFace(f, FACE_COLORS[i % FACE_COLORS.length], w, h));

    const now = performance.now();
    if (sendFaces && now - lastFacesSent > 1000 / FACES_MSG_HZ) {
      lastFacesSent = now;
      sendFaces({
        count: faces.length,
        looking_at_agent: faces.filter((f) => f.looking_at_agent).length,
        faces: faces.map((f) => ({
          i: f.i,
          primary: !!f.primary,
          looking_at_agent: f.looking_at_agent,
          head_yaw: Math.round(f.yaw),
          head_pitch: Math.round(f.pitch),
          eyes: Math.round(f.eyes * 100) / 100,
          size: Math.round(f.area * 100) / 100,
        })),
      });
    }
    render();
  }

  function onUserState(s) {
    state = s;
    try {
      if (send(s)) sent += 1;
    } catch (e) {
      console.warn("user_state send failed", e);
    }
    render();
  }

  function row(label, value, cls = "") {
    return `<div class="fr ${cls}"><span>${label}</span><b>${value}</b></div>`;
  }

  function render() {
    const s = state;
    const calib = performance.now() - startedAt;
    const looking = faces.filter((f) => f.looking_at_agent).length;
    const rows = [
      row("faces in frame", faces.length || "none", faces.length ? "ok" : "warn"),
      row("looking at agent", faces.length ? `${looking} of ${faces.length}` : "—", looking ? "ok" : "warn"),
    ];
    for (const f of faces) {
      rows.push(
        row(
          `face ${f.i + 1}${f.primary ? " (primary)" : ""}`,
          `${f.looking_at_agent ? "at agent" : "away"} · ${Math.round(f.yaw)}° / ${Math.round(f.pitch)}°`,
          f.looking_at_agent ? "ok" : "",
        ),
      );
    }
    rows.push(
      row("nod", s ? s.nod : "—"),
      row("wants turn", s ? (s.wants_turn ? "yes" : "no") : "—"),
      row("confusion_p", s ? s.confusion_p.toFixed(2) : "—"),
      row("brow_lower / lip_press Δ", s ? `${s.au.brow_lower.toFixed(2)} / ${s.au.lip_press.toFixed(2)}` : "—"),
      row(
        "prosody: pitch Δ / rate Δ / pause",
        s ? `${s.prosody_delta.pitch.toFixed(1)}Hz / ${s.prosody_delta.rate.toFixed(2)} / ${s.prosody_delta.pause_ms.toFixed(0)}ms` : "—",
      ),
      row("baseline", calib < CALIBRATION_MS ? `calibrating… ${Math.ceil((CALIBRATION_MS - calib) / 1000)}s` : "ready (deltas)"),
      row("user_state sent", sent),
    );
    readout.innerHTML = rows.join("");
  }

  async function start() {
    readout.innerHTML = row("face tracking", "loading MediaPipe…");
    const base = { video, onUserState, onResult, audio, numFaces: MAX_FACES };
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
