import {
  FaceLandmarker,
  FilesetResolver,
  type FaceLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { matrixToEulerDeg } from "./headPose";
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
  wasmBaseUrl?: string;
  modelAssetPath?: string;
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
    numFaces: 1,
    outputFaceBlendshapes: true,
    outputFacialTransformationMatrixes: true,
  });

  const sampler = new UserStateSampler();
  let stopped = false;
  let rafId = 0;

  const loop = () => {
    if (stopped) return;
    const t_ms = performance.now();
    if (opts.video.readyState >= 2) {
      const result = landmarker.detectForVideo(opts.video, t_ms);
      const frame = toRawFaceFrame(t_ms, result);
      opts.onResult?.(result, frame);
      const state = sampler.ingest(frame);
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
    },
  };
}
