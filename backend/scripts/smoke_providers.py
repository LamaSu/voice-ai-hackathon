"""Check every provider credential from .env and print latencies.

Usage: uv run python scripts/smoke_providers.py [--models gpt-oss-120b,gemma-4-31B-it]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI  # noqa: E402
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.providers import gradium_raw  # noqa: E402


async def check_jev(n: int = 8) -> None:
    s = get_settings()
    state = {
        "bot": {"speaking": True, "current_sentence": "Tomorrow will be sunny with a high of"},
        "user": {"partial_transcript": "no wait, I said Saturday", "speech_ms": 900},
    }
    questions = {
        "intent": Choice(
            instructions="What is the user doing while the bot talks?",
            criteria={"interrupt": None, "backchannel": None, "side_talk": None, "noise": None},
        ),
        "wants_floor": Noul(instructions="Is the user trying to take the conversational turn?"),
    }
    lat = []
    async with AsyncTypeSafeClient(api_key=s.jev_api_key, model=s.jev_model) as client:
        for i in range(n):
            t0 = time.perf_counter()
            r = await client.system_one(state, questions)
            lat.append((time.perf_counter() - t0) * 1000)
            if i == 0:
                print(f"[jev] model={r.model} answers={ {k: v.model_dump() for k, v in r.answers.items()} }")
    print(f"[jev] latency ms p50={statistics.median(lat):.0f} min={min(lat):.0f} max={max(lat):.0f}")


async def check_gradium() -> None:
    s = get_settings()
    phrase = "Actually, what about Saturday instead?"
    pcm, sr, ttfa = await gradium_raw.tts(phrase, s.gradium_api_key, s.gradium_tts_voice)
    print(f"[gradium tts] {len(pcm) / sr:.2f}s audio @ {sr}Hz, first audio {ttfa * 1000:.0f} ms")
    pcm16 = gradium_raw.resample_int16(pcm, sr, 16000)
    text, lat = await gradium_raw.stt(pcm16, s.gradium_api_key)
    print(f"[gradium stt] '{text}' (flush->final {lat * 1000:.0f} ms)")


async def check_llm(models: list[str]) -> None:
    s = get_settings()
    client = AsyncOpenAI(api_key=s.general_compute_api_key, base_url=s.general_compute_base_url)
    for model in models:
        t0 = time.perf_counter()
        ttft = None
        out = []
        try:
            stream = await client.chat.completions.create(
                model=model,
                stream=True,
                max_tokens=80,
                messages=[
                    {"role": "system", "content": "You are a concise voice assistant. One sentence."},
                    {"role": "user", "content": "What's a good thing to do on a sunny Saturday?"},
                ],
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    ttft = ttft or time.perf_counter() - t0
                    out.append(delta)
            total = time.perf_counter() - t0
            ttft_ms = f"{ttft * 1000:.0f}" if ttft else "n/a"
            print(f"[llm {model}] ttft={ttft_ms} ms total={total * 1000:.0f} ms: {''.join(out)[:120]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"[llm {model}] FAILED: {e}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gpt-oss-120b,gemma-4-31B-it,minimax-m2.7")
    args = ap.parse_args()
    for name, coro in (
        ("jev", check_jev()),
        ("gradium", check_gradium()),
        ("llm", check_llm(args.models.split(","))),
    ):
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
