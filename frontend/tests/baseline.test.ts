import { describe, expect, it } from "vitest";
import { BaselineTracker } from "../src/gaze/baseline";

describe("BaselineTracker", () => {
  it("is not ready before the window elapses", () => {
    const tracker = new BaselineTracker<{ x: number }>(30_000);
    tracker.addSample(0, { x: 1 });
    tracker.addSample(10_000, { x: 1 });
    expect(tracker.isReady()).toBe(false);
  });

  it("locks in the mean of the window as baseline once it elapses", () => {
    const tracker = new BaselineTracker<{ x: number }>(1000);
    tracker.addSample(0, { x: 0 });
    tracker.addSample(500, { x: 2 });
    tracker.addSample(1000, { x: 4 }); // mean = 2 -> baseline locks here
    expect(tracker.isReady()).toBe(true);
    expect(tracker.delta({ x: 5 })).toEqual({ x: 3 });
  });

  it("does not keep updating the baseline after it locks", () => {
    const tracker = new BaselineTracker<{ x: number }>(1000);
    tracker.addSample(0, { x: 0 });
    tracker.addSample(1000, { x: 2 }); // baseline = 1
    tracker.addSample(2000, { x: 100 }); // should be ignored
    expect(tracker.delta({ x: 1 })).toEqual({ x: 0 });
  });

  it("deltas against the running mean before the baseline locks", () => {
    const tracker = new BaselineTracker<{ x: number }>(30_000);
    tracker.addSample(0, { x: 2 });
    tracker.addSample(1000, { x: 4 }); // running mean = 3
    expect(tracker.delta({ x: 4 })).toEqual({ x: 1 });
  });

  it("resets cleanly", () => {
    const tracker = new BaselineTracker<{ x: number }>(1000);
    tracker.addSample(0, { x: 0 });
    tracker.addSample(1000, { x: 2 });
    expect(tracker.isReady()).toBe(true);
    tracker.reset();
    expect(tracker.isReady()).toBe(false);
  });
});
