type Features = Record<string, number>;

function averageFeatures<T extends Features>(samples: T[]): T {
  const keys = Object.keys(samples[0] ?? {});
  const result = {} as T;
  for (const key of keys) {
    (result as Features)[key] =
      samples.reduce((sum, s) => sum + (s[key] ?? 0), 0) / samples.length;
  }
  return result;
}

// Contract 1 defines values as deltas from a 30s baseline. During that
// window there's no fixed baseline yet, so we delta against the running
// mean of samples seen so far rather than emitting raw (un-delta'd) values.
export class BaselineTracker<T extends Features> {
  private samples: T[] = [];
  private baseline: T | null = null;
  private startTMs: number | null = null;

  constructor(private readonly windowMs: number = 30_000) {}

  addSample(t_ms: number, features: T): void {
    if (this.baseline) return;
    if (this.startTMs === null) this.startTMs = t_ms;
    this.samples.push(features);
    if (t_ms - this.startTMs >= this.windowMs) {
      this.baseline = averageFeatures(this.samples);
    }
  }

  isReady(): boolean {
    return this.baseline !== null;
  }

  delta(features: T): T {
    const base = this.baseline ?? averageFeatures(this.samples.length ? this.samples : [features]);
    const result = {} as T;
    for (const key of Object.keys(features) as (keyof T)[]) {
      (result as Features)[key as string] = (features[key] as number) - ((base[key] as number) ?? 0);
    }
    return result;
  }

  reset(): void {
    this.samples = [];
    this.baseline = null;
    this.startTMs = null;
  }
}
