import {
  FaceLandmarker,
  FilesetResolver,
  type FaceLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { matrixToEulerDeg } from "./headPose";
import { ProsodySampler } from "./prosody";
import { UserStateSampler } from "./sampler";
import type { RawFaceFrame, UserState } from "./types";

export type { UserState, AUDelta, ProsodyDelta, RawFaceFrame } from "./types";
export { isGazeAway } from "./headPose";

const DEFAULT_WASM_BASE_URL =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const DEFAULT_MODEL_ASSET_PATH =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

export interface GazeTrackerOptions {
  video: HTMLVideoElement;
  onUserState: (state: UserState) => void;
  // Optional (added by lane A for the on-screen landmark/gaze overlay): the raw
  // per-frame MediaPipe result and derived head pose. Stays in the browser.
  onResult?: (result: FaceLandmarkerResult, frame: RawFaceFrame) => void;
  // Optional (C3, #10): the same mic track the call already opened, for
  // vocal-prosody fusion into confusion_p. Read-only — this never plays the
  // audio back or opens a second getUserMedia call. Omit it (or pass a
  // falsy value) to keep face-only behavior.
  audio?: MediaStreamTrack | MediaStream | null;
  wasmBaseUrl?: string;
  modelAssetPath?: string;
  // How many faces to track (lane A: the room can hold more than one person).
  // user_state (Contract 1) still describes one person: the largest face in frame.
  numFaces?: number;
}

export interface GazeTracker {
  stop(): void;
}

function toRawFaceFrame(t_ms: number, result: FaceLandmarkerResult): RawFaceFrame {
  const facePresent = (result.faceBlendshapes?.length ?? 0) > 0;
  const blendshapes: Record<string, number> = {};
  if (facePresent) {
    for (const category of result.faceBlendshapes![0].categories) {
      blendshapes[category.categoryName] = category.score;
    }
  }

  let headYawDeg = 0;
  let headPitchDeg = 0;
  let headRollDeg = 0;
  if (facePresent && result.facialTransformationMatrixes?.length) {
    const euler = matrixToEulerDeg(result.facialTransformationMatrixes[0].data);
    headYawDeg = euler.yaw;
    headPitchDeg = euler.pitch;
    headRollDeg = euler.roll;
  }

  return { t_ms, facePresent, blendshapes, headYawDeg, headPitchDeg, headRollDeg };
}

// Runs MediaPipe Face Landmarker on a live <video> element and emits
// user_state (Contract 1) at ~10Hz via onUserState. Only numeric features
// leave this module — no video/image data is ever passed to onUserState.
export async function createGazeTracker(opts: GazeTrackerOptions): Promise<GazeTracker> {
  const fileset = await FilesetResolver.forVisionTasks(
    opts.wasmBaseUrl ?? DEFAULT_WASM_BASE_URL,
  );
  const landmarker = await FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: opts.modelAssetPath ?? DEFAULT_MODEL_ASSET_PATH,
      delegate: "GPU",
    },
    runningMode: "VIDEO",
    numFaces: opts.numFaces ?? 1,
    outputFaceBlendshapes: true,
    outputFacialTransformationMatrixes: true,
  });

  const sampler = new UserStateSampler();

  let prosodySampler: ProsodySampler | null = null;
  let audioCtx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let pcmBuffer: Float32Array<ArrayBuffer> | null = null;
  if (opts.audio) {
    audioCtx = new AudioContext();
    const stream = opts.audio instanceof MediaStreamTrack ? new MediaStream([opts.audio]) : opts.audio;
    const source = audioCtx.createMediaStreamSource(stream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser); // analysis only — never connected to a destination, so it's silent
    pcmBuffer = new Float32Array(analyser.fftSize);
    prosodySampler = new ProsodySampler();
  }

  let stopped = false;
  let rafId = 0;

  const loop = () => {
    if (stopped) return;
    const t_ms = performance.now();
    if (opts.video.readyState >= 2) {
      const result = landmarker.detectForVideo(opts.video, t_ms);
      const frame = toRawFaceFrame(t_ms, result);
      opts.onResult?.(result, frame);

      let prosody;
      if (analyser && pcmBuffer && prosodySampler && audioCtx) {
        analyser.getFloatTimeDomainData(pcmBuffer);
        prosody = prosodySampler.ingest(t_ms, pcmBuffer, audioCtx.sampleRate);
      }

      const state = sampler.ingest(frame, prosody);
      if (state) opts.onUserState(state);
    }
    rafId = requestAnimationFrame(loop);
  };
  rafId = requestAnimationFrame(loop);

  return {
    stop() {
      stopped = true;
      cancelAnimationFrame(rafId);
      landmarker.close();
      audioCtx?.close();
    },
  };
}
