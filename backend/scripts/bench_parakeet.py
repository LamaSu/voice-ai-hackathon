"""Benchmark local Parakeet (MLX) against the Gradium cloud STT on our fixtures.

Measures what actually matters for barge-in: how long after a word is spoken it appears
in the transcript, plus the real-time factor of streaming inference.

Usage: uv run python scripts/bench_parakeet.py [--chunk 0.32]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.providers import gradium_raw  # noqa: E402

FIXTURES = BACKEND_DIR / "tests" / "fixtures"
MODEL_DIR = BACKEND_DIR / "models" / "parakeet-tdt-0.6b-v3"
CLIPS = ["intro", "story", "interrupt", "yeah", "second_person"]


def load_wav(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def bench_parakeet(chunk_s: float) -> dict[str, dict]:
    import mlx.core as mx
    from parakeet_mlx import from_pretrained

    t0 = time.perf_counter()
    model = from_pretrained(str(MODEL_DIR))
    print(f"[parakeet] model loaded in {time.perf_counter() - t0:.1f}s from {MODEL_DIR.name}")

    out: dict[str, dict] = {}
    for name in CLIPS:
        audio = load_wav(name)
        dur = len(audio) / 16000
        n = int(chunk_s * 16000)
        chunk_times: list[float] = []
        first_word_lag = None
        text = ""
        t_start = time.perf_counter()
        with model.transcribe_stream(context_size=(256, 256)) as stream:
            for i in range(0, len(audio), n):
                piece = audio[i : i + n]
                audio_end_s = (i + len(piece)) / 16000  # when this audio would have been spoken
                t_c = time.perf_counter()
                stream.add_audio(mx.array(piece))
                chunk_times.append(time.perf_counter() - t_c)
                text = stream.result.text.strip()
                if text and first_word_lag is None:
                    # compute time spent so far minus the audio consumed = lag behind the speaker
                    first_word_lag = sum(chunk_times) - audio_end_s + chunk_s
        total = time.perf_counter() - t_start
        out[name] = {
            "text": text,
            "audio_s": round(dur, 2),
            "compute_s": round(total, 2),
            "rtf": round(total / dur, 3),
            "chunk_ms_p50": round(statistics.median(chunk_times) * 1000),
            "chunk_ms_max": round(max(chunk_times) * 1000),
            "first_text_lag_s": round(first_word_lag, 2) if first_word_lag is not None else None,
            "final_lag_s": round(chunk_times[-1], 2),
        }
        print(f"[parakeet] {name}: {out[name]}")
    return out


async def bench_gradium() -> dict[str, dict]:
    s = get_settings()
    out: dict[str, dict] = {}
    for name in CLIPS:
        audio = load_wav(name)
        pcm = (audio * 32768).astype(np.int16)
        t0 = time.perf_counter()
        text, flush_lag = await gradium_raw.stt(pcm, s.gradium_api_key, realtime=True)
        out[name] = {
            "text": text,
            "audio_s": round(len(audio) / 16000, 2),
            "wall_s": round(time.perf_counter() - t0, 2),
            "final_after_last_audio_s": round(flush_lag, 2),
        }
        print(f"[gradium]  {name}: {out[name]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=float, default=0.32, help="streaming chunk size in seconds")
    ap.add_argument("--skip-gradium", action="store_true")
    args = ap.parse_args()

    p = bench_parakeet(args.chunk)
    g = {} if args.skip_gradium else asyncio.run(bench_gradium())

    print("\n=== transcripts ===")
    for name in CLIPS:
        print(f"\n{name}:")
        print(f"  parakeet: {p[name]['text']!r}")
        if g:
            print(f"  gradium : {g[name]['text']!r}")
    print("\n=== speed ===")
    print(f"{'clip':16} {'audio':>6} {'parakeet rtf':>13} {'chunk p50':>10} {'final lag':>10} {'gradium final lag':>18}")
    for name in CLIPS:
        gl = f"{g[name]['final_after_last_audio_s']:.2f}s" if g else "-"
        print(
            f"{name:16} {p[name]['audio_s']:5.2f}s {p[name]['rtf']:13.3f} "
            f"{p[name]['chunk_ms_p50']:9}ms {p[name]['final_lag_s']:9.2f}s {gl:>18}"
        )


if __name__ == "__main__":
    main()
