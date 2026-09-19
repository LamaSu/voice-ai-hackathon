#!/usr/bin/env python3
"""Which pipeline stage is eating the latency? Reads metrics.jsonl and ranks them.

We instrumented this precisely so nobody has to guess at 4pm. Every turn writes
a Contract 4 sample with a per-stage breakdown from Pipecat's
UserBotLatencyObserver; this adds them up across turns and prints the worst
offender first, with what each one actually means and what moves it.

    ./scripts/where_time_goes.py               # reads ./metrics.jsonl
    ./scripts/where_time_goes.py path.jsonl
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CONVERSATIONAL_MS = 500
PHONE_TREE_MS = 1500

# What each stage is, and the knob that moves it. Matched on substring, because
# Pipecat's contribution keys carry processor names.
WHAT_MOVES_IT = {
    "endpoint": "VAD stop_secs (bot.py, currently 0.3 s) — dead air before we even start",
    "wait": "turn-taking: how long we wait to be sure the user finished",
    "transcri": "Gradium STT finalisation after VAD stop",
    "llm": "model choice and prompt length — a memory-laden system prompt costs TTFT",
    "inference": "model choice and prompt length — a memory-laden system prompt costs TTFT",
    "synth": "Gradium TTS first audio; also how much text we buffer before speaking",
    "aggregat": "sentence aggregation — we may be waiting for a full sentence before TTS starts",
    "marker": "turn-completion marker tokens generated before any speakable text",
    "pipeline": "frame hops; only worth attention if it is large",
}


def explain(key: str) -> str:
    low = key.lower()
    for needle, text in WHAT_MOVES_IT.items():
        if needle in low:
            return text
    return ""


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "metrics.jsonl")
    if not path.exists():
        print(f"No {path}. Run a few turns first — every turn appends one line.")
        return 1

    totals: list[float] = []
    stages: dict[str, list[float]] = defaultdict(list)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        total = row.get("end_of_speech_to_first_audio_ms")
        if isinstance(total, (int, float)):
            totals.append(total)
        for key, ms in (row.get("stages") or {}).items():
            if isinstance(ms, (int, float)):
                stages[key].append(ms)

    if not totals:
        print(f"{path} has no latency samples yet.")
        return 1

    median = statistics.median(totals)
    verdict = (
        "conversational" if median < CONVERSATIONAL_MS
        else "usable" if median < PHONE_TREE_MS
        else "PHONE TREE — the wrong side of the rubric"
    )
    print(f"\n{len(totals)} turns in {path}")
    print(f"end of speech → first audio: median {median:.0f} ms  [{verdict}]")
    print(f"  best {min(totals):.0f} ms, worst {max(totals):.0f} ms\n")

    if not stages:
        print("No per-stage breakdown recorded — enable_metrics must be on in PipelineParams.")
        return 0

    ranked = sorted(stages.items(), key=lambda kv: statistics.median(kv[1]), reverse=True)
    print("Where it goes, worst first (median per turn):\n")
    for key, values in ranked:
        med = statistics.median(values)
        share = (med / median * 100) if median else 0
        bar = "█" * max(1, round(share / 3))
        print(f"  {key:<26}{med:>8.0f} ms  {share:>4.0f}%  {bar}")
        note = explain(key)
        if note and share >= 15:
            print(f"  {'':<26}         ↳ {note}")

    top, top_values = ranked[0]
    top_med = statistics.median(top_values)
    print(f"\nBiggest single win: {top} at {top_med:.0f} ms/turn.")
    if median > PHONE_TREE_MS:
        need = median - PHONE_TREE_MS
        print(f"Need to find {need:.0f} ms just to reach 1.5 s, {median - CONVERSATIONAL_MS:.0f} ms for the 500 ms target.")
    print("Fix the top row before touching anything below it.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
