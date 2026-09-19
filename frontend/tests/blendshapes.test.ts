import { describe, expect, it } from "vitest";
import { extractAURaw } from "../src/gaze/blendshapes";

describe("extractAURaw", () => {
  it("averages left/right brow-lower and lip-press blendshapes", () => {
    const result = extractAURaw({
      browDownLeft: 0.4,
      browDownRight: 0.6,
      mouthPressLeft: 0.2,
      mouthPressRight: 0.0,
      jawOpen: 0.3,
    });
    expect(result.brow_lower).toBeCloseTo(0.5);
    expect(result.lip_press).toBeCloseTo(0.1);
    expect(result.mouth_open).toBeCloseTo(0.3);
  });

  it("defaults missing categories to 0", () => {
    const result = extractAURaw({});
    expect(result).toEqual({ brow_lower: 0, lip_press: 0, mouth_open: 0 });
  });
});
