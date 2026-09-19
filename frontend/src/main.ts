import { createGazeTracker, type UserState } from "./gaze";

const CALIBRATION_MS = 30_000;

const app = document.getElementById("app")!;
app.innerHTML = `
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; background: #111; color: #eee; }
    #layout { display: flex; gap: 1rem; padding: 1rem; }
    video { width: 480px; border-radius: 8px; transform: scaleX(-1); }
    pre { background: #000; padding: 1rem; border-radius: 8px; flex: 1; overflow: auto; max-height: 480px; }
    #calibration { font-size: 1.1rem; margin-bottom: 0.5rem; }
  </style>
  <div id="layout">
    <div>
      <div id="calibration">Starting camera…</div>
      <video id="video" autoplay playsinline muted></video>
    </div>
    <pre id="output">waiting for first frame…</pre>
  </div>
`;

const video = document.getElementById("video") as HTMLVideoElement;
const output = document.getElementById("output") as HTMLPreElement;
const calibration = document.getElementById("calibration") as HTMLDivElement;

async function main() {
  const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  video.srcObject = stream;
  await video.play();

  const startedAt = performance.now();

  await createGazeTracker({
    video,
    onUserState: (state: UserState) => {
      const elapsed = performance.now() - startedAt;
      calibration.textContent =
        elapsed < CALIBRATION_MS
          ? `Calibrating baseline… ${Math.ceil((CALIBRATION_MS - elapsed) / 1000)}s left`
          : "Baseline ready — values below are deltas.";
      output.textContent = JSON.stringify(state, null, 2);
    },
  });
}

main().catch((err) => {
  calibration.textContent = `Camera/model error: ${String(err)}`;
});
