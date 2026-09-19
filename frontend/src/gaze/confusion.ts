import type { AUDelta, ProsodyDelta } from "./types";

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}

export const ZERO_PROSODY: ProsodyDelta = { pitch: 0, rate: 0, pause_ms: 0 };

// A long pause and a slower-than-baseline rate both read as hesitation —
// "I lost the thread" — independent of what the face is doing. Doesn't use
// pitch: rising/falling pitch alone is too ambiguous (could be a question,
// could be emphasis) to treat as a confusion signal on its own.
function prosodyHesitation(prosody: ProsodyDelta): number {
  const longPause = clamp01(prosody.pause_ms / 1500);
  const sloweddown = clamp01(-prosody.rate);
  return 0.6 * longPause + 0.4 * sloweddown;
}

// This is a prior confirmed by probe questions, not a verdict — see
// COORDINATION.md's decision log. Face still carries most of the weight
// since it's the more directly validated signal; prosody (C3) nudges it
// rather than overriding it.
export function estimateConfusion(
  au: AUDelta,
  gazeAway: boolean,
  prosody: ProsodyDelta = ZERO_PROSODY,
): number {
  const face = Math.max(au.brow_lower, 0) * 0.6 + Math.max(au.lip_press, 0) * 0.4;
  const gazePenalty = gazeAway ? 0.15 : 0;
  const hesitation = prosodyHesitation(prosody);
  return clamp01(0.7 * clamp01(face - gazePenalty) + 0.3 * hesitation);
}

export function estimateWantsTurn(mouthOpenDelta: number, gazeAway: boolean): boolean {
  return mouthOpenDelta > 0.15 && !gazeAway;
}
