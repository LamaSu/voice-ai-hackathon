import { describe, expect, it } from "vitest";
import { isGazeAway, matrixToEulerDeg, NodDetector } from "../src/gaze/headPose";

describe("isGazeAway", () => {
  it("is false when looking roughly at the camera", () => {
    expect(isGazeAway(5, 5)).toBe(false);
  });

  it("is true past the yaw threshold", () => {
    expect(isGazeAway(25, 0)).toBe(true);
  });

  it("is true past the pitch threshold", () => {
    expect(isGazeAway(0, 20)).toBe(true);
  });
});

describe("matrixToEulerDeg", () => {
  it("returns all zeros for an identity matrix", () => {
    // prettier-ignore
    const identity = [
      1, 0, 0, 0,
      0, 1, 0, 0,
      0, 0, 1, 0,
      0, 0, 0, 1,
    ];
    const { yaw, pitch, roll } = matrixToEulerDeg(identity);
    expect(yaw).toBeCloseTo(0);
    expect(pitch).toBeCloseTo(0);
    expect(roll).toBeCloseTo(0);
  });

  it("recovers a 90 degree yaw rotation", () => {
    // Rotation of +90deg about Y: x' = z, z' = -x
    // prettier-ignore
    const rotY90 = [
      0, 0, 1, 0,
      0, 1, 0, 0,
      -1, 0, 0, 0,
      0, 0, 0, 1,
    ];
    const { yaw } = matrixToEulerDeg(rotY90);
    expect(Math.abs(yaw)).toBeCloseTo(90, 0);
  });
});

describe("NodDetector", () => {
  it("reports 0 nods for a still head", () => {
    const detector = new NodDetector(1500, 8);
    let last = 0;
    for (let t = 0; t <= 1000; t += 100) {
      last = detector.addSample({ t_ms: t, pitchDeg: 0 });
    }
    expect(last).toBe(0);
  });

  it("counts one down-up swing as a nod", () => {
    const detector = new NodDetector(1500, 8);
    const pitches = [0, 0, -15, -20, -15, 0, 5];
    let last = 0;
    pitches.forEach((pitchDeg, i) => {
      last = detector.addSample({ t_ms: i * 100, pitchDeg });
    });
    expect(last).toBeGreaterThanOrEqual(1);
  });
});
