import { describe, expect, it } from "vitest";
import {
  estimatePitchHz,
  PauseTracker,
  ProsodySampler,
  SyllableRateEstimator,
} from "../src/gaze/prosody";

const SAMPLE_RATE = 16_000;

function sineWave(freqHz: number, n: number, sampleRate = SAMPLE_RATE, amplitude = 0.5): Float32Array {
  const buffer = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    buffer[i] = amplitude * Math.sin((2 * Math.PI * freqHz * i) / sampleRate);
  }
  return buffer;
}

function silence(n: number): Float32Array {
  return new Float32Array(n);
}

describe("estimatePitchHz", () => {
  it("recovers the frequency of a clean sine wave", () => {
    const buffer = sineWave(150, 2048);
    const hz = estimatePitchHz(buffer, SAMPLE_RATE);
    expect(hz).not.toBeNull();
    expect(Math.abs(hz! - 150)).toBeLessThan(10); // lag quantization, not exact
  });

  it("returns null for silence", () => {
    expect(estimatePitchHz(silence(2048), SAMPLE_RATE)).toBeNull();
  });
});

describe("SyllableRateEstimator", () => {
  it("is 0 with too little history", () => {
    const estimator = new SyllableRateEstimator();
    expect(estimator.addSample({ t_ms: 0, rms: 0.1 })).toBe(0);
  });

  it("counts amplitude-envelope peaks per second", () => {
    const estimator = new SyllableRateEstimator(2000, 0.02);
    // Two clear peaks (0.1) separated by dips (0.0) over 1 second.
    const pattern = [0.1, 0.0, 0.1, 0.0, 0.1];
    let last = 0;
    pattern.forEach((r, i) => {
      last = estimator.addSample({ t_ms: i * 250, rms: r });
    });
    expect(last).toBeGreaterThan(0);
  });

  it("resets cleanly", () => {
    const estimator = new SyllableRateEstimator();
    estimator.addSample({ t_ms: 0, rms: 0.1 });
    estimator.addSample({ t_ms: 100, rms: 0.1 });
    estimator.reset();
    expect(estimator.addSample({ t_ms: 0, rms: 0.1 })).toBe(0);
  });
});

describe("PauseTracker", () => {
  it("is 0 while speaking", () => {
    const tracker = new PauseTracker();
    expect(tracker.addSample(0, 0.1)).toBe(0);
    expect(tracker.addSample(500, 0.1)).toBe(0);
  });

  it("grows once speech stops", () => {
    const tracker = new PauseTracker();
    tracker.addSample(0, 0.1); // speaking
    expect(tracker.addSample(500, 0.0)).toBe(500);
    expect(tracker.addSample(900, 0.0)).toBe(900);
  });

  it("is 0 before any speech has been seen", () => {
    const tracker = new PauseTracker();
    expect(tracker.addSample(1000, 0.0)).toBe(0);
  });

  it("resets cleanly", () => {
    const tracker = new PauseTracker();
    tracker.addSample(0, 0.1);
    tracker.reset();
    expect(tracker.addSample(500, 0.0)).toBe(0);
  });
});

describe("ProsodySampler", () => {
  it("reports zero pitch delta and growing pause_ms once silence follows speech", () => {
    const sampler = new ProsodySampler();
    sampler.ingest(0, sineWave(150, 2048), SAMPLE_RATE); // establishes "speaking" for the pause tracker
    const a = sampler.ingest(200, silence(2048), SAMPLE_RATE);
    const b = sampler.ingest(500, silence(2048), SAMPLE_RATE);
    expect(b.pitch).toBe(a.pitch); // silence doesn't move the pitch baseline
    expect(b.pause_ms).toBeGreaterThan(a.pause_ms);
  });

  it("pause_ms drops back to 0 once voiced speech resumes", () => {
    const sampler = new ProsodySampler();
    sampler.ingest(0, silence(2048), SAMPLE_RATE);
    sampler.ingest(800, silence(2048), SAMPLE_RATE);
    const voiced = sampler.ingest(1000, sineWave(150, 2048), SAMPLE_RATE);
    expect(voiced.pause_ms).toBe(0);
  });

  it("pitch delta rises once the baseline locks and pitch goes up", () => {
    const sampler = new ProsodySampler();
    // Establish a ~150Hz baseline over 30s of voiced frames.
    for (let t = 0; t <= 30_000; t += 1000) {
      sampler.ingest(t, sineWave(150, 2048), SAMPLE_RATE);
    }
    const higher = sampler.ingest(31_000, sineWave(220, 2048), SAMPLE_RATE);
    expect(higher.pitch).toBeGreaterThan(0);
  });

  it("resets cleanly", () => {
    const sampler = new ProsodySampler();
    sampler.ingest(0, silence(2048), SAMPLE_RATE);
    sampler.ingest(500, silence(2048), SAMPLE_RATE);
    sampler.reset();
    const fresh = sampler.ingest(0, silence(2048), SAMPLE_RATE);
    expect(fresh.pause_ms).toBe(0);
  });
});
