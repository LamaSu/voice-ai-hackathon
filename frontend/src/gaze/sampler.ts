import { extractAURaw } from "./blendshapes";
import { BaselineTracker } from "./baseline";
import { isGazeAway, NodDetector } from "./headPose";
import { estimateConfusion, estimateWantsTurn, ZERO_PROSODY } from "./confusion";
import type { AURaw, ProsodyDelta, RawFaceFrame, UserState } from "./types";

const DEFAULT_INTERVAL_MS = 100; // ~10 Hz, per Contract 1

export class UserStateSampler {
  private readonly auBaseline = new BaselineTracker<AURaw>();
  private readonly nodDetector = new NodDetector();
  private lastEmitMs = -Infinity;

  constructor(private readonly intervalMs: number = DEFAULT_INTERVAL_MS) {}

  // Returns a UserState when it's time to emit at the ~10Hz cadence, or
  // null when this frame should be dropped to stay near the target rate.
  // `prosody` (C3) is optional: callers without a mic track available yet
  // just get face-only confusion_p, same as before C3 landed.
  ingest(frame: RawFaceFrame, prosody: ProsodyDelta = ZERO_PROSODY): UserState | null {
    if (frame.t_ms - this.lastEmitMs < this.intervalMs) return null;
    this.lastEmitMs = frame.t_ms;

    if (!frame.facePresent) {
      return {
        t_ms: frame.t_ms,
        au: { brow_lower: 0, lip_press: 0 },
        gaze_away: true,
        nod: 0,
        wants_turn: false,
        prosody_delta: prosody,
        confusion_p: 0,
      };
    }

    const auRaw = extractAURaw(frame.blendshapes);
    this.auBaseline.addSample(frame.t_ms, auRaw);
    const auDelta = this.auBaseline.delta(auRaw);

    const gazeAway = isGazeAway(frame.headYawDeg, frame.headPitchDeg);
    const nod = this.nodDetector.addSample({ t_ms: frame.t_ms, pitchDeg: frame.headPitchDeg });

    const au = { brow_lower: auDelta.brow_lower, lip_press: auDelta.lip_press };

    return {
      t_ms: frame.t_ms,
      au,
      gaze_away: gazeAway,
      nod,
      wants_turn: estimateWantsTurn(auDelta.mouth_open, gazeAway),
      prosody_delta: prosody,
      confusion_p: estimateConfusion(au, gazeAway, prosody),
    };
  }

  reset(): void {
    this.auBaseline.reset();
    this.nodDetector.reset();
    this.lastEmitMs = -Infinity;
  }
}
