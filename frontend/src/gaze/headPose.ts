export interface HeadPoseSample {
  t_ms: number;
  pitchDeg: number;
}

export const GAZE_AWAY_YAW_THRESHOLD_DEG = 20;
export const GAZE_AWAY_PITCH_THRESHOLD_DEG = 15;

export function isGazeAway(yawDeg: number, pitchDeg: number): boolean {
  return (
    Math.abs(yawDeg) > GAZE_AWAY_YAW_THRESHOLD_DEG ||
    Math.abs(pitchDeg) > GAZE_AWAY_PITCH_THRESHOLD_DEG
  );
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}

// MediaPipe FaceLandmarker's facialTransformationMatrixes[0].data is a
// row-major 4x4 matrix. Extract Tait-Bryan yaw/pitch/roll from its
// rotation submatrix.
export function matrixToEulerDeg(m: ArrayLike<number>): {
  yaw: number;
  pitch: number;
  roll: number;
} {
  const m00 = m[0],
    m01 = m[1];
  const m11 = m[5];
  const m20 = m[8],
    m21 = m[9],
    m22 = m[10];

  const pitch = Math.asin(clamp(-m21, -1, 1));
  let yaw: number;
  let roll: number;
  if (Math.abs(m21) < 0.9999) {
    yaw = Math.atan2(m20, m22);
    roll = Math.atan2(m01, m11);
  } else {
    // Gimbal lock: roll and yaw trade off, roll is taken as 0.
    yaw = Math.atan2(-m01, m00);
    roll = 0;
  }
  const rad2deg = 180 / Math.PI;
  return { yaw: yaw * rad2deg, pitch: pitch * rad2deg, roll: roll * rad2deg };
}

// Counts down-then-up (or up-then-down) pitch swings within a rolling
// window as a rough nod count. Heuristic, not validated against ground
// truth yet.
export class NodDetector {
  private history: HeadPoseSample[] = [];

  constructor(
    private readonly windowMs = 1500,
    private readonly minSwingDeg = 8,
  ) {}

  addSample(sample: HeadPoseSample): number {
    this.history.push(sample);
    this.history = this.history.filter((s) => sample.t_ms - s.t_ms <= this.windowMs);
    return this.countNods();
  }

  reset(): void {
    this.history = [];
  }

  private countNods(): number {
    if (this.history.length < 3) return 0;
    let nods = 0;
    let dir = 0; // -1 down, 1 up, 0 unknown
    let extremum = this.history[0].pitchDeg;
    for (let i = 1; i < this.history.length; i++) {
      const diff = this.history[i].pitchDeg - extremum;
      let newDir = dir;
      if (diff > this.minSwingDeg) newDir = 1;
      else if (diff < -this.minSwingDeg) newDir = -1;

      if (newDir !== dir) {
        // A nod completes when the head returns up (1) after having gone
        // down (-1); the initial neutral-to-down transition isn't a nod yet.
        if (dir === -1 && newDir === 1) nods += 1;
        extremum = this.history[i].pitchDeg;
        dir = newDir;
      }
    }
    return nods;
  }
}
