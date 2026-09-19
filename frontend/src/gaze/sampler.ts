import { extractAURaw } from "./blendshapes";
import { BaselineTracker } from "./baseline";
import { isGazeAway, NodDetector } from "./headPose";
import { estimateConfusion, estimateWantsTurn } from "./confusion";
import type { AURaw, RawFaceFrame, UserState } from "./types";

const DEFAULT_INTERVAL_MS = 100; // ~10 Hz, per Contract 1

export class UserStateSampler {
  private readonly auBaseline = new BaselineTracker<AURaw>();
  private readonly nodDetector = new NodDetector();
  private lastEmitMs = -Infinity;

  constructor(private readonly intervalMs: number = DEFAULT_INTERVAL_MS) {}

  // Returns a UserState when it's time to emit at the ~10Hz cadence, or
  // null when this frame should be dropped to stay near the target rate.
  ingest(frame: RawFaceFrame): UserState | null {
    if (frame.t_ms - this.lastEmitMs < this.intervalMs) return null;
    this.lastEmitMs = frame.t_ms;

    if (!frame.facePresent) {
      return {
        t_ms: frame.t_ms,
        au: { brow_lower: 0, lip_press: 0 },
        gaze_away: true,
        nod: 0,
        wants_turn: false,
        prosody_delta: { pitch: 0, rate: 0, pause_ms: 0 },
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
      // Audio-derived; stub until C3 (prosody fusion) lands.
      prosody_delta: { pitch: 0, rate: 0, pause_ms: 0 },
      confusion_p: estimateConfusion(au, gazeAway),
    };
  }

  reset(): void {
    this.auBaseline.reset();
    this.nodDetector.reset();
    this.lastEmitMs = -Infinity;
  }
}
