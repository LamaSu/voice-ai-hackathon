import { describe, expect, it } from "vitest";
import { estimateConfusion, estimateWantsTurn } from "../src/gaze/confusion";

describe("estimateConfusion", () => {
  it("is 0 for a neutral face looking at the camera", () => {
    expect(estimateConfusion({ brow_lower: 0, lip_press: 0 }, false)).toBe(0);
  });

  it("rises with brow lowering and lip pressing", () => {
    expect(estimateConfusion({ brow_lower: 0.5, lip_press: 0.5 }, false)).toBeCloseTo(0.5);
  });

  it("clamps to [0, 1]", () => {
    expect(estimateConfusion({ brow_lower: 10, lip_press: 10 }, false)).toBe(1);
  });

  it("is penalized, not boosted, when looking away", () => {
    const looking = estimateConfusion({ brow_lower: 0.5, lip_press: 0 }, false);
    const away = estimateConfusion({ brow_lower: 0.5, lip_press: 0 }, true);
    expect(away).toBeLessThan(looking);
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
