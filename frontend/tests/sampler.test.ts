import { describe, expect, it } from "vitest";
import { UserStateSampler } from "../src/gaze/sampler";
import type { RawFaceFrame } from "../src/gaze/types";

function frame(overrides: Partial<RawFaceFrame> = {}): RawFaceFrame {
  return {
    t_ms: 0,
    facePresent: true,
    blendshapes: {},
    headYawDeg: 0,
    headPitchDeg: 0,
    headRollDeg: 0,
    ...overrides,
  };
}

describe("UserStateSampler", () => {
  it("emits a state matching Contract 1's exact shape", () => {
    const sampler = new UserStateSampler();
    const state = sampler.ingest(frame());
    expect(state).not.toBeNull();
    expect(Object.keys(state!).sort()).toEqual(
      ["au", "confusion_p", "gaze_away", "nod", "prosody_delta", "t_ms", "wants_turn"].sort(),
    );
    expect(Object.keys(state!.au).sort()).toEqual(["brow_lower", "lip_press"].sort());
    expect(Object.keys(state!.prosody_delta).sort()).toEqual(
      ["pause_ms", "pitch", "rate"].sort(),
    );
  });

  it("throttles to roughly the configured interval", () => {
    const sampler = new UserStateSampler(100);
    expect(sampler.ingest(frame({ t_ms: 0 }))).not.toBeNull();
    expect(sampler.ingest(frame({ t_ms: 50 }))).toBeNull();
    expect(sampler.ingest(frame({ t_ms: 120 }))).not.toBeNull();
  });

  it("reports gaze_away true and zeroed features when no face is present", () => {
    const sampler = new UserStateSampler();
    const state = sampler.ingest(frame({ facePresent: false }));
    expect(state).toEqual({
      t_ms: 0,
      au: { brow_lower: 0, lip_press: 0 },
      gaze_away: true,
      nod: 0,
      wants_turn: false,
      prosody_delta: { pitch: 0, rate: 0, pause_ms: 0 },
      confusion_p: 0,
    });
  });

  it("resets baseline and nod state on reset()", () => {
    const sampler = new UserStateSampler(100);
    sampler.ingest(frame({ t_ms: 0, blendshapes: { browDownLeft: 0.5, browDownRight: 0.5 } }));
    sampler.reset();
    const state = sampler.ingest(frame({ t_ms: 0, blendshapes: { browDownLeft: 0.5, browDownRight: 0.5 } }));
    // Fresh baseline means the first sample deltas against itself -> 0.
    expect(state!.au.brow_lower).toBeCloseTo(0);
  });
});
