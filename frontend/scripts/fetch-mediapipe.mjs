// Copies MediaPipe's WASM runtime out of node_modules and downloads the Face Landmarker model
// into public/mediapipe, so face tracking works without a CDN (venue wifi).
// Runs automatically before `npm run dev` / `npm run build`. Safe to re-run.
import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "mediapipe");
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

mkdirSync(out, { recursive: true });
const wasmSrc = join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm");
if (existsSync(wasmSrc)) cpSync(wasmSrc, join(out, "wasm"), { recursive: true });

const model = join(out, "face_landmarker.task");
if (!existsSync(model)) {
  try {
    const res = await fetch(MODEL_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    writeFileSync(model, Buffer.from(await res.arrayBuffer()));
    console.log("mediapipe: downloaded face_landmarker.task");
  } catch (e) {
    console.warn(`mediapipe: model download failed (${e.message}); the app will use the CDN`);
  }
}
