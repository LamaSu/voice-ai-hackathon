#!/usr/bin/env python3
"""Explain a session log: what happened, turn by turn, and what went wrong.

A JSONL file nobody can read at 5pm is not telemetry. This turns one into a
narrative plus a list of anomalies, so "the agent did something weird on the
third question" becomes a specific line with a timestamp.

    ./scripts/diagnose.py                       # newest session in backend/logs
    ./scripts/diagnose.py backend/logs/session-20260919-1701.jsonl
    ./scripts/diagnose.py --turns                # per-turn timeline only
    ./scripts/diagnose.py --problems             # anomalies only
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "backend" / "logs"

CONVERSATIONAL_MS = 500
PHONE_TREE_MS = 1500


def newest_log() -> Path | None:
    logs = sorted(LOG_DIR.glob("session-*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def load(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  ! line {i} is not valid JSON, skipped")
    return rows


def show_context(rows: list[dict]) -> None:
    start = next((r for r in rows if r.get("type") == "session_start"), None)
    print("\n\033[1mSession\033[0m")
    if not start:
        print("  no session_start record — this log predates telemetry, so the")
        print("  model, thresholds and commit behind it are unknown.")
        return
    for key in ("git_sha", "llm_model", "stt_engine", "jev_model", "jev_timeout_s",
                "enable_fillers", "enable_speaker_id", "pipecat", "platform"):
        if key in start:
            print(f"  {key:<24}{start[key]}")
    missing = [k.replace("has_", "") for k in ("has_gradium_key", "has_general_compute_key", "has_jev_key")
               if k in start and not start[k]]
    if missing:
        print(f"  \033[33mkeys absent:\033[0m {', '.join(missing)} (fallbacks would have been used)")


def show_turns(rows: list[dict]) -> None:
    by_turn: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_turn[r.get("turn", 0)].append(r)

    print("\n\033[1mTurns\033[0m")
    for turn in sorted(by_turn):
        if turn == 0:
            continue
        events = by_turn[turn]
        said = next((e.get("text") for e in events if e.get("type") == "transcript" and e.get("text")), None)
        metrics = next((e for e in events if e.get("type") == "metrics"), None)
        jev = [e for e in events if e.get("type") == "jev"]
        filler = next((e for e in events if e.get("event") == "filler"), None)

        first = f"{metrics['end_of_speech_to_first_audio_ms']:.0f}ms" if metrics and metrics.get(
            "end_of_speech_to_first_audio_ms") else "—"
        answer = metrics.get("end_of_speech_to_first_content_ms") if metrics else None
        answer_s = f", answer {answer:.0f}ms" if answer and metrics and answer != metrics.get(
            "end_of_speech_to_first_audio_ms") else ""

        print(f"\n  \033[1mTurn {turn}\033[0m  first audio {first}{answer_s}")
        if said:
            print(f"    user: {said[:90]!r}")
        if filler:
            print(f"    filler: [{filler.get('category')}] {filler.get('text')!r} ({filler.get('duration_s')}s)")
        for j in jev[-3:]:
            d = j.get("decision") or {}
            print(f"    jev {j.get('kind', '?')}: {d.get('action', '?')} ({d.get('reason', '?')})")


def show_problems(rows: list[dict]) -> int:
    print("\n\033[1mProblems\033[0m")
    found = 0

    errors = [r for r in rows if r.get("type") == "log"]
    for e in errors:
        colour = "31" if e.get("level") == "ERROR" else "33"
        print(f"  \033[{colour}m{e.get('level')}\033[0m t+{e.get('t')}s {e.get('where')}: {e.get('message')}")
        found += 1

    client = [r for r in rows if r.get("type") == "client"]
    for c in client:
        if str(c.get("level", "")).lower() in ("error", "warn", "warning"):
            print(f"  \033[33mBROWSER\033[0m t+{c.get('t')}s {c.get('message')}")
            found += 1

    # Jev unavailable / fallbacks: the agent still answers, but differently.
    fallbacks = [r for r in rows if "fallback" in str((r.get("decision") or {}).get("reason", ""))]
    if fallbacks:
        print(f"  \033[33m{len(fallbacks)} turn(s)\033[0m used a deterministic fallback "
              f"(Jev slow, unavailable, or unsure)")
        found += 1

    lat = [r.get("end_of_speech_to_first_audio_ms") for r in rows if r.get("type") == "metrics"]
    lat = [v for v in lat if isinstance(v, (int, float))]
    if lat:
        med = statistics.median(lat)
        slow = [v for v in lat if v > PHONE_TREE_MS]
        colour = "32" if med < CONVERSATIONAL_MS else ("33" if med < PHONE_TREE_MS else "31")
        print(f"  latency median \033[{colour}m{med:.0f} ms\033[0m over {len(lat)} turns; "
              f"{len(slow)} over the {PHONE_TREE_MS} ms line")
        if slow:
            found += 1

    if not rows or not any(r.get("type") == "metrics" for r in rows):
        print("  \033[33mno latency samples\033[0m — either no turns completed, or metrics are off")
        found += 1

    if not found:
        print("  \033[32mnothing anomalous\033[0m")
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", help="session jsonl (default: newest in backend/logs)")
    ap.add_argument("--turns", action="store_true", help="turn timeline only")
    ap.add_argument("--problems", action="store_true", help="anomalies only")
    args = ap.parse_args()

    path = Path(args.log) if args.log else newest_log()
    if not path or not path.exists():
        print(f"No session log found in {LOG_DIR}. Run a session first.")
        return 1

    rows = load(path)
    print(f"\n{path}  —  {len(rows)} records")
    if not rows:
        print("Empty log.")
        return 1

    everything = not (args.turns or args.problems)
    if everything:
        show_context(rows)
    if everything or args.turns:
        show_turns(rows)
    if everything or args.problems:
        show_problems(rows)

    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r.get("type", "?")] += 1
    if everything:
        print("\n\033[1mRecord counts\033[0m")
        for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:<16}{n}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
