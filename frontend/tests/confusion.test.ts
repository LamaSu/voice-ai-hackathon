import { describe, expect, it } from "vitest";
import { estimateConfusion, estimateWantsTurn } from "../src/gaze/confusion";
import type { ProsodyDelta } from "../src/gaze/types";

const ZERO: ProsodyDelta = { pitch: 0, rate: 0, pause_ms: 0 };

describe("estimateConfusion", () => {
  it("is 0 for a neutral face looking at the camera with no prosody signal", () => {
    expect(estimateConfusion({ brow_lower: 0, lip_press: 0 }, false)).toBe(0);
  });

  it("rises with brow lowering and lip pressing", () => {
    // face-only: 0.6*brow + 0.4*lip = 0.5, weighted 0.7 in the fused score
    expect(estimateConfusion({ brow_lower: 0.5, lip_press: 0.5 }, false)).toBeCloseTo(0.35);
  });

  it("face alone caps below 1 now that prosody has a share of the score", () => {
    expect(estimateConfusion({ brow_lower: 10, lip_press: 10 }, false)).toBeCloseTo(0.7);
  });

  it("clamps to 1 when both face and prosody hesitation are maxed", () => {
    const hesitant: ProsodyDelta = { pitch: 0, rate: -5, pause_ms: 5000 };
    expect(estimateConfusion({ brow_lower: 10, lip_press: 10 }, false, hesitant)).toBe(1);
  });

  it("is penalized, not boosted, when looking away", () => {
    const looking = estimateConfusion({ brow_lower: 0.5, lip_press: 0 }, false);
    const away = estimateConfusion({ brow_lower: 0.5, lip_press: 0 }, true);
    expect(away).toBeLessThan(looking);
  });

  it("a long pause raises confusion on a neutral face", () => {
    const neutral = estimateConfusion({ brow_lower: 0, lip_press: 0 }, false, ZERO);
    const hesitant = estimateConfusion({ brow_lower: 0, lip_press: 0 }, false, { pitch: 0, rate: 0, pause_ms: 1200 });
    expect(hesitant).toBeGreaterThan(neutral);
  });

  it("speaking slower than baseline raises confusion", () => {
    const neutral = estimateConfusion({ brow_lower: 0, lip_press: 0 }, false, ZERO);
    const slowed = estimateConfusion({ brow_lower: 0, lip_press: 0 }, false, { pitch: 0, rate: -1, pause_ms: 0 });
    expect(slowed).toBeGreaterThan(neutral);
  });

  it("speaking faster than baseline does not raise confusion", () => {
    const faster = estimateConfusion({ brow_lower: 0, lip_press: 0 }, false, { pitch: 0, rate: 1, pause_ms: 0 });
    expect(faster).toBe(0);
  });
});

describe("estimateWantsTurn", () => {
  it("is true when the mouth opens while looking at the camera", () => {
    expect(estimateWantsTurn(0.3, false)).toBe(true);
  });

  it("is false when looking away, even with mouth open", () => {
    expect(estimateWantsTurn(0.3, true)).toBe(false);
  });

  it("is false for a small mouth-open delta", () => {
    expect(estimateWantsTurn(0.05, false)).toBe(false);
  });
});
