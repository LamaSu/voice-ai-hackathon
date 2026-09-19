import type { AURaw } from "./types";

const BROW_LOWER_KEYS = ["browDownLeft", "browDownRight"];
const LIP_PRESS_KEYS = ["mouthPressLeft", "mouthPressRight"];
const MOUTH_OPEN_KEY = "jawOpen";

function avg(values: number[]): number {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

function pick(record: Record<string, number>, keys: string[]): number[] {
  return keys.map((k) => record[k] ?? 0);
}

export function extractAURaw(blendshapes: Record<string, number>): AURaw {
  return {
    brow_lower: avg(pick(blendshapes, BROW_LOWER_KEYS)),
    lip_press: avg(pick(blendshapes, LIP_PRESS_KEYS)),
    mouth_open: blendshapes[MOUTH_OPEN_KEY] ?? 0,
  };
}
