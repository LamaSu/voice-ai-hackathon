import { BaselineTracker } from "./baseline";
import type { ProsodyDelta } from "./types";

// C3 decision (derived-from-audio, not Hume): estimating pitch/rate/pause
// on-device from the mic track the call already opened needs no new vendor,
// no new key, and no extra network hop on the speech path — which matters
// with median end-of-speech-to-first-audio already over budget. Hume would
// mean a per-utterance API call in a path we're trying to make faster, not
// slower. See the decision log in COORDINATION.md.

function rms(buffer: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < buffer.length; i++) sum += buffer[i] * buffer[i];
  return Math.sqrt(sum / buffer.length);
}

const SILENCE_RMS = 0.01;

// Autocorrelation F0 estimate over one analyser frame. Returns null when the
// frame is too quiet to have a pitch (silence, breath, unvoiced consonants).
// Heuristic, not validated against ground truth — good enough to detect
// relative shifts from a person's own baseline, not absolute accuracy.
export function estimatePitchHz(
  buffer: Float32Array,
  sampleRate: number,
  minHz = 70,
  maxHz = 400,
): number | null {
  if (rms(buffer) < SILENCE_RMS) return null;

  const minLag = Math.floor(sampleRate / maxHz);
  const maxLag = Math.min(Math.floor(sampleRate / minHz), buffer.length - 1);
  let bestLag = -1;
  let bestCorr = 0;
  for (let lag = minLag; lag <= maxLag; lag++) {
    let corr = 0;
    for (let i = 0; i < buffer.length - lag; i++) {
      corr += buffer[i] * buffer[i + lag];
    }
    if (corr > bestCorr) {
      bestCorr = corr;
      bestLag = lag;
    }
  }
  return bestLag > 0 ? sampleRate / bestLag : null;
}

export interface EnvelopeSample {
  t_ms: number;
  rms: number;
}

// Counts amplitude-envelope peaks per second as a speaking-rate proxy.
// A real "words per second" needs word boundaries we don't have client-side
// without running ASR twice; syllable-nuclei rate is the standard cheap
// substitute in prosody research, and it's what's derivable from raw audio
// alone.
export class SyllableRateEstimator {
  private history: EnvelopeSample[] = [];

  constructor(
    private readonly windowMs = 2000,
    private readonly minPeakRms = 0.02,
  ) {}

  addSample(sample: EnvelopeSample): number {
    this.history.push(sample);
    this.history = this.history.filter((s) => sample.t_ms - s.t_ms <= this.windowMs);
    return this.ratePerSecond();
  }

  reset(): void {
    this.history = [];
  }

  private ratePerSecond(): number {
    if (this.history.length < 3) return 0;
    let peaks = 0;
    for (let i = 1; i < this.history.length - 1; i++) {
      const prev = this.history[i - 1].rms;
      const cur = this.history[i].rms;
      const next = this.history[i + 1].rms;
      if (cur >= this.minPeakRms && cur > prev && cur >= next) peaks++;
    }
    const spanMs = this.history[this.history.length - 1].t_ms - this.history[0].t_ms;
    return spanMs > 0 ? peaks / (spanMs / 1000) : 0;
  }
}

// Current pause duration in ms (0 while speaking). Contract 1's own example
// (`pause_ms: 800`) is a plain duration, not a baseline delta, so this
// reports the raw gap rather than delta-ing it against a baseline.
export class PauseTracker {
  private lastSpeechAtMs: number | null = null;

  constructor(private readonly speechRmsThreshold = 0.02) {}

  addSample(t_ms: number, sampleRms: number): number {
    if (sampleRms >= this.speechRmsThreshold) {
      this.lastSpeechAtMs = t_ms;
      return 0;
    }
    return this.lastSpeechAtMs === null ? 0 : t_ms - this.lastSpeechAtMs;
  }

  reset(): void {
    this.lastSpeechAtMs = null;
  }
}

// Combines the three signals above into Contract 1's prosody_delta shape.
// Pitch is only baseline-updated on voiced frames — feeding silence in as
// "0 Hz" would drag the baseline toward zero and make every voiced frame
// look like a huge upward spike.
export class ProsodySampler {
  private readonly pitchBaseline = new BaselineTracker<{ pitch_hz: number }>();
  private readonly rateBaseline = new BaselineTracker<{ rate_hz: number }>();
  private readonly rateEstimator = new SyllableRateEstimator();
  private readonly pauseTracker = new PauseTracker();
  private lastPitchDelta = 0;

  ingest(t_ms: number, buffer: Float32Array, sampleRate: number): ProsodyDelta {
    const sampleRms = rms(buffer);
    const pauseMs = this.pauseTracker.addSample(t_ms, sampleRms);
    const rateHz = this.rateEstimator.addSample({ t_ms, rms: sampleRms });

    const pitchHz = estimatePitchHz(buffer, sampleRate);
    if (pitchHz !== null) {
      this.pitchBaseline.addSample(t_ms, { pitch_hz: pitchHz });
      this.lastPitchDelta = this.pitchBaseline.delta({ pitch_hz: pitchHz }).pitch_hz;
    }

    this.rateBaseline.addSample(t_ms, { rate_hz: rateHz });
    const rateDelta = this.rateBaseline.delta({ rate_hz: rateHz }).rate_hz;

    return { pitch: this.lastPitchDelta, rate: rateDelta, pause_ms: pauseMs };
  }

  reset(): void {
    this.pitchBaseline.reset();
    this.rateBaseline.reset();
    this.rateEstimator.reset();
    this.pauseTracker.reset();
    this.lastPitchDelta = 0;
  }
}
