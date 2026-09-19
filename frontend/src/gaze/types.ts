// Shape mirrors Contract 1 (`user_state`, C -> B) in COORDINATION.md exactly.
// Do not add or rename fields here without a contract-change issue.

export interface AUDelta {
  brow_lower: number;
  lip_press: number;
}

export interface ProsodyDelta {
  pitch: number;
  rate: number;
  pause_ms: number;
}

export interface UserState {
  t_ms: number;
  au: AUDelta;
  gaze_away: boolean;
  nod: number;
  wants_turn: boolean;
  prosody_delta: ProsodyDelta;
  confusion_p: number;
}

export interface RawFaceFrame {
  t_ms: number;
  facePresent: boolean;
  // MediaPipe FaceLandmarker blendshape category name -> score in [0, 1].
  blendshapes: Record<string, number>;
  headYawDeg: number;
  headPitchDeg: number;
  headRollDeg: number;
}

export interface AURaw {
  [key: string]: number;
  brow_lower: number;
  lip_press: number;
  mouth_open: number;
}
