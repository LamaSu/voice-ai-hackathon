"""End-to-end test of background agents over real WebRTC.

The behaviour being checked is the whole point of the feature:

  1. "Set a timer for ten seconds." -> Jev spins a timer agent, it appears in telemetry,
     and the bot says it's on it *without* waiting ten seconds.
  2. The conversation keeps working while the agent runs: a second, unrelated question
     gets a normal answer.
  3. When the agent finishes, its result is spoken — at a gap, not over anyone.
  4. "What's Apple trading at today?" -> a stock agent fetches a real price and speaks it.

Usage:
  uv run python -m app.server &
  uv run python scripts/e2e_agents.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.e2e_webrtc import BotEar, MicTrack, load_clip, wait_until  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7860")
    args = ap.parse_args()

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
        r = await http.post(f"{args.url}/api/offer", json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
        ans = r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    await asyncio.wait_for(opened.wait(), 15)
    dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                        "data": {"version": "1.0.0", "about": {"library": "e2e-agents"}}}))
    await asyncio.sleep(3.0)

    results: dict = {}
    failures: list[str] = []

    def tasks_now() -> list[dict]:
        for e in reversed(events):
            if e.get("type") == "tasks":
                return e.get("tasks") or []
        return []

    def announcements() -> list[dict]:
        return [e for e in events if e.get("type") == "interaction" and e.get("event") == "announcement"]

    async def say(clip: str) -> float:
        mic.play(load_clip(clip))
        await mic.clip_done.wait()
        return time.perf_counter()

    # ---- 1. ask for a timer -------------------------------------------------------
    print("\n[1] 'Set a timer for ten seconds.'")
    t_end = await say("set_timer")
    if not await wait_until(lambda: any(t["kind"] == "timer" for t in tasks_now()), 6):
        failures.append("no timer agent appeared in telemetry")
    else:
        rec = next(t for t in tasks_now() if t["kind"] == "timer")
        results["agent"] = (rec["kind"], rec["title"], rec["status"])
    ok = await wait_until(lambda: ear.first_audio_after(t_end) is not None, 8)
    if ok:
        ack = ear.first_audio_after(t_end) - t_end
        results["acknowledged_after_s"] = round(ack, 2)
        if ack > 4.0:
            failures.append(f"the bot waited {ack:.1f}s to acknowledge — it should reply immediately")
    else:
        failures.append("the bot never acknowledged the request")

    # ---- 2. keep talking while it runs --------------------------------------------
    await asyncio.sleep(2.0)
    print("[2] meanwhile: \"What's the capital of Japan?\"")
    before = len(events)
    t_end2 = await say("capital")
    got = await wait_until(
        lambda: any(
            e.get("type") == "transcript" and e.get("role") == "bot" and "tokyo" in (e.get("text") or "").lower()
            for e in events[before:]
        ),
        15,
    )
    results["answered_while_agent_running"] = got
    if not got:
        failures.append("the conversation stalled while the agent was running")
    running = [t for t in tasks_now() if t["status"] == "running"]
    results["still_running_during_chat"] = [t["title"] for t in running]

    # ---- 3. the timer fires and is spoken -----------------------------------------
    print("[3] waiting for the timer to fire...")
    fired = await wait_until(lambda: any("timer" in a["text"].lower() for a in announcements()), 20)
    results["timer_announcement"] = announcements()[-1]["text"] if announcements() else None
    if not fired:
        failures.append("the timer result was never spoken")
    else:
        done = [t for t in tasks_now() if t["kind"] == "timer"]
        results["timer_elapsed_s"] = done[0]["elapsed_s"] if done else None

    # ---- 4. a stock agent fetches real data ----------------------------------------
    await asyncio.sleep(1.5)
    print("[4] \"What's Apple trading at today?\"")
    before = len(events)
    await say("stock_price")
    got_stock = await wait_until(
        lambda: any(
            e.get("type") == "interaction" and e.get("event") == "announcement" and
            any(c.isdigit() for c in (e.get("text") or ""))
            for e in events[before:]
        ),
        25,
    )
    stock_tasks = [t for t in tasks_now() if t["kind"] == "stock"]
    results["stock_agent"] = (stock_tasks[0]["title"], stock_tasks[0]["status"]) if stock_tasks else None
    results["stock_announcement"] = next(
        (e["text"] for e in reversed(events) if e.get("event") == "announcement" and any(c.isdigit() for c in e.get("text", ""))),
        None,
    )
    if not got_stock:
        failures.append("the stock agent never reported a price")

    results["telemetry"] = [(t["kind"], t["title"], t["status"], t["elapsed_s"]) for t in tasks_now()]
    print("\n===== RESULTS =====")
    for k, v in results.items():
        if isinstance(v, list) and v and isinstance(v[0], tuple):
            print(f"{k}:")
            for row in v:
                print(f"    {row}")
        else:
            print(f"{k}: {v}")
    print("\nFAILURES:" if failures else "\nALL CHECKS PASSED", *failures, sep="\n  - ")
    await pc.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
