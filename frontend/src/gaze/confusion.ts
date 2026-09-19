import type { AUDelta } from "./types";

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}

// Face-only prior; COORDINATION.md's decision log treats this as a prior
// confirmed by probe questions, not a verdict. Prosody fusion (issue C3,
// optional) refines this once it lands.
export function estimateConfusion(au: AUDelta, gazeAway: boolean): number {
  const raw = Math.max(au.brow_lower, 0) * 0.6 + Math.max(au.lip_press, 0) * 0.4;
  const gazePenalty = gazeAway ? 0.15 : 0;
  return clamp01(raw - gazePenalty);
}

export function estimateWantsTurn(mouthOpenDelta: number, gazeAway: boolean): boolean {
  return mouthOpenDelta > 0.15 && !gazeAway;
}
