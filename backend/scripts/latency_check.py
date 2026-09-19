"""Measure time-to-first-token per model on General Compute, and look for
evidence about which hardware serves them (#15, #16).

Two questions this answers, in one run:

1. **Which model can we actually demo?** The rubric line is 500 ms end of
   speech to first audio; TTFT is only one part of that budget, so a model
   whose TTFT alone exceeds it is disqualified regardless of the rest.
2. **Is it SambaNova?** Inference must run on SambaNova hardware or we are
   ineligible. This prints whatever the provider says about itself — the
   models payload and the response headers — so a human can judge rather
   than us assuming.

It reports the **median** of several trials, not a best-of. One lucky call is
not a number to put in front of a judge, and the first call of a session is
usually slow.

Usage (needs GENERAL_COMPUTE_API_KEY in the repo-root .env):

    uv run python scripts/latency_check.py
    uv run python scripts/latency_check.py --models minimax-m2.7 --trials 9
    uv run python scripts/latency_check.py --json > latency.json

Cost: trials x models short completions, capped at 64 output tokens each.
The default run is well under a cent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Run as `uv run python scripts/latency_check.py` from backend/ without needing
# PYTHONPATH set: scripts/ is the cwd's child, app/ is its sibling.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

# A prompt shaped like the demo: the agent mid-explanation. Latency on "hi" is
# not the latency we ship, because prompt length moves time to first token.
PROMPT = (
    "You are explaining how a transformer's attention mechanism works to a "
    "curious beginner, out loud, in conversation. Give the next two sentences."
)

# Rubric thresholds, from COORDINATION.md.
CONVERSATIONAL_MS = 500
PHONE_TREE_MS = 1500

# Substrings that would indicate the serving hardware in metadata.
HARDWARE_HINTS = ("sambanova", "sn40", "sn50", "rdu", "cerebras", "groq", "nvidia", "h100", "a100")


async def measure(client, model: str, trials: int) -> dict[str, Any]:
    """Time to first token and total, repeated, with the first run discarded."""
    ttfts: list[float] = []
    totals: list[float] = []
    error: str | None = None
    sample = ""

    # trials + 1: the first call pays connection setup and any cold start, and
    # is thrown away rather than allowed to skew the median.
    for i in range(trials + 1):
        t0 = time.perf_counter()
        ttft = None
        chunks: list[str] = []
        try:
            stream = await client.chat.completions.create(
                model=model,
                stream=True,
                max_tokens=64,
                messages=[{"role": "user", "content": PROMPT}],
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    chunks.append(delta)
            total = time.perf_counter() - t0
        except Exception as e:  # noqa: BLE001 - one dead model must not end the run
            error = f"{type(e).__name__}: {e}"
            break

        if i == 0:
            continue  # warmup
        if ttft is not None:
            ttfts.append(ttft * 1000)
            totals.append(total * 1000)
            sample = "".join(chunks)

    return {
        "model": model,
        "error": error,
        "n": len(ttfts),
        "ttft_ms": summarize(ttfts),
        "total_ms": summarize(totals),
        "sample": sample[:100],
    }


def summarize(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 1),
        "min": round(ordered[0], 1),
        "max": round(ordered[-1], 1),
        # With few trials this is the worst observed rather than a true p95;
        # named honestly so nobody quotes it as more than it is.
        "worst": round(ordered[-1], 1),
    }


def verdict(ttft_median: float | None) -> str:
    if ttft_median is None:
        return "no data"
    if ttft_median < CONVERSATIONAL_MS:
        return "TTFT fits inside the 500 ms budget (the rest of the pipeline still has to)"
    if ttft_median < PHONE_TREE_MS:
        return "over the 500 ms target on TTFT alone; usable but not conversational"
    return "TTFT alone exceeds the 1.5 s phone-tree line — not demoable"


async def probe_hardware(client, base_url: str) -> dict[str, Any]:
    """Anything the provider volunteers about what serves these models."""
    out: dict[str, Any] = {"base_url": base_url, "hints": [], "models": None, "headers": {}}
    try:
        listing = await client.models.list()
        names = [m.id for m in listing.data]
        out["models"] = names
        blob = json.dumps([m.model_dump() for m in listing.data]).lower()
        out["hints"] = sorted({h for h in HARDWARE_HINTS if h in blob})
    except Exception as e:  # noqa: BLE001
        out["models_error"] = f"{type(e).__name__}: {e}"

    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=(out.get("models") or ["minimax-m2.7"])[0],
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        interesting = {
            k: v
            for k, v in raw.headers.items()
            if any(t in k.lower() for t in ("server", "hardware", "backend", "provider", "x-"))
        }
        out["headers"] = interesting
        blob = json.dumps(interesting).lower()
        out["hints"] = sorted(set(out["hints"]) | {h for h in HARDWARE_HINTS if h in blob})
    except Exception as e:  # noqa: BLE001
        out["headers_error"] = f"{type(e).__name__}: {e}"
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="minimax-m2.7,gemma-4-31B-it")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="machine-readable output only")
    args = ap.parse_args()

    from openai import AsyncOpenAI

    s = get_settings()
    client = AsyncOpenAI(api_key=s.general_compute_api_key, base_url=s.general_compute_base_url)

    hw = await probe_hardware(client, s.general_compute_base_url)
    results = [await measure(client, m.strip(), args.trials) for m in args.models.split(",")]

    if args.json:
        print(json.dumps({"hardware": hw, "results": results}, indent=2))
        return 0

    print(f"\nGeneral Compute — {s.general_compute_base_url}")
    print(f"{args.trials} timed trials per model, first call discarded as warmup\n")
    print(f"{'model':<24}{'TTFT median':>14}{'min':>9}{'worst':>9}{'total med':>12}")
    print("-" * 68)
    for r in results:
        if r["error"] or not r["ttft_ms"]:
            print(f"{r['model']:<24}  FAILED: {r['error']}")
            continue
        t, tot = r["ttft_ms"], r["total_ms"]
        print(
            f"{r['model']:<24}{t['median']:>11.0f} ms{t['min']:>8.0f}{t['worst']:>9.0f}"
            f"{tot['median']:>10.0f} ms"
        )

    print()
    for r in results:
        if r["ttft_ms"]:
            print(f"  {r['model']}: {verdict(r['ttft_ms']['median'])}")

    print("\nHardware evidence (#15 — eligibility):")
    if hw.get("models"):
        print(f"  models offered: {', '.join(hw['models'][:12])}")
    if hw.get("headers"):
        for k, v in sorted(hw["headers"].items()):
            print(f"  header {k}: {v}")
    if hw["hints"]:
        print(f"  >> hardware mentioned in metadata: {', '.join(hw['hints'])}")
    else:
        print("  >> nothing in the API metadata names the hardware.")
        print("     The API cannot settle #15. Ask General Compute directly and get it in writing.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
