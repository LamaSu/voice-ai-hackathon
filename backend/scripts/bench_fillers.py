"""Measure what a cached filler does to perceived latency.

Asks the same question N times over real WebRTC and reports, from the end of the user's
speech: when the user first hears ANYTHING (the filler, if Jev picked one), and when the
LLM's actual answer starts.

Usage:
  ENABLE_FILLERS=1 uv run python -m app.server &
  uv run python scripts/bench_fillers.py --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.e2e_webrtc import BotEar, MicTrack, load_clip, wait_until  # noqa: E402


async def one_run(url: str, clip: str) -> dict:
    events: list[dict] = []
    pc = RTCPeerConnection()
    mic = MicTrack()
    pc.addTrack(mic)
    ear = BotEar()
    dc = pc.createDataChannel("chat")
    opened = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            asyncio.ensure_future(ear.consume(track))

    @dc.on("open")
    def on_open():
        opened.set()

    @dc.on("message")
    def on_message(msg):
        try:
            m = json.loads(msg)
        except Exception:
            return
        if m.get("label") == "rtvi-ai" and m.get("type") == "server-message":
            d = m.get("data") or {}
            d["_rx"] = time.perf_counter()
            events.append(d)

    await pc.setLocalDescription(await pc.createOffer())
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(f"{url}/api/offer", json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
        ans = r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    await asyncio.wait_for(opened.wait(), 15)
    dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                        "data": {"version": "1.0.0", "about": {"library": "bench"}}}))
    await asyncio.sleep(3.0)

    mic.play(load_clip(clip))
    await mic.clip_done.wait()
    t_end = time.perf_counter()

    await wait_until(lambda: ear.first_audio_after(t_end) is not None, 15)
    first = ear.first_audio_after(t_end)
    filler = next((e for e in events if e.get("type") == "interaction" and e.get("event") == "filler"), None)
    # the answer is the first audio after any filler clip has finished
    answer_after = (first + filler["duration_s"]) if filler and first else t_end
    await wait_until(lambda: ear.first_audio_after(answer_after + 0.15) is not None, 15)
    answer = ear.first_audio_after(answer_after + 0.15) if filler else first
    await pc.close()
    return {
        "first_audio_s": round(first - t_end, 2) if first else None,
        "answer_audio_s": round(answer - t_end, 2) if answer else None,
        "filler": f"{filler['category']}:{filler['text']}" if filler else None,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7860")
    ap.add_argument("--clip", default="why_purr")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    rows = []
    for i in range(args.runs):
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(f"{args.url}/api/memory/reset")
        row = await one_run(args.url, args.clip)
        print(f"run {i + 1}: {row}")
        rows.append(row)
        await asyncio.sleep(1.0)

    firsts = [r["first_audio_s"] for r in rows if r["first_audio_s"]]
    answers = [r["answer_audio_s"] for r in rows if r["answer_audio_s"]]
    print(
        f"\nfirst sound  median {statistics.median(firsts):.2f}s  ({min(firsts):.2f}-{max(firsts):.2f})"
        f"\nanswer audio median {statistics.median(answers):.2f}s  ({min(answers):.2f}-{max(answers):.2f})"
        f"\nfillers: {[r['filler'] for r in rows]}"
    )


if __name__ == "__main__":
    asyncio.run(main())
