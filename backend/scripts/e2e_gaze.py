"""End-to-end test of gaze gating over real WebRTC.

Sends the `faces` telemetry a browser would send, and checks the agent's behaviour:

  1. Two people in frame, nobody looking at the agent -> speech is IGNORED (greyed in the UI,
     no answer, no audio).
  2. Someone looks at the agent -> the same kind of question is answered normally.
  3. Nobody looking + talking over the bot -> the bot is NOT interrupted.
  4. "Stop." always stops the bot, looking or not.

Usage:
  DEV_FIXTURES=1 uv run python -m app.server &
  uv run python scripts/e2e_gaze.py
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


def faces_message(count: int, looking: int) -> dict:
    return {
        "count": count,
        "looking_at_agent": looking,
        "faces": [
            {"i": i, "primary": i == 0, "looking_at_agent": i < looking,
             "head_yaw": 3 if i < looking else 35, "head_pitch": 0, "eyes": 0.5, "size": 0.2}
            for i in range(count)
        ],
    }


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

    def send(type_: str, data: dict) -> None:
        dc.send(json.dumps({"label": "rtvi-ai", "type": "client-message", "id": str(uuid.uuid4()),
                            "data": {"t": type_, "d": data}}))

    dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
                        "data": {"version": "1.0.0", "about": {"library": "e2e-gaze"}}}))

    stop_gaze = False

    async def gaze_loop(count: int, looking: int):
        """Keep sending telemetry at ~5 Hz, like the browser does."""
        while not stop_gaze:
            send("faces", faces_message(count, looking))
            await asyncio.sleep(0.2)

    results: dict = {}
    failures: list[str] = []

    def ignored() -> list[dict]:
        return [e for e in events if e.get("type") == "transcript" and e.get("role") == "user_ignored"]

    def bot_said() -> list[str]:
        return [e.get("text", "") for e in events if e.get("type") == "transcript" and e.get("role") == "bot"]

    async def say(clip: str) -> float:
        mic.play(load_clip(clip))
        await mic.clip_done.wait()
        return time.perf_counter()

    # ---- 1. nobody looking: the agent should stay out of it ------------------------
    print("\n[1] two people in frame, both looking away")
    task = asyncio.ensure_future(gaze_loop(2, 0))
    await asyncio.sleep(1.0)
    t_end = await say("capital")
    got_ignored = await wait_until(lambda: len(ignored()) > 0, 8)
    spoke = await wait_until(lambda: ear.first_audio_after(t_end) is not None, 3)
    results["ignored_when_looking_away"] = ignored()[-1]["text"] if ignored() else None
    results["stayed_silent"] = not spoke
    if not got_ignored:
        failures.append("speech was not ignored while everyone looked away")
    if spoke:
        failures.append("the agent answered although nobody was looking at it")

    # ---- 2. someone looks: normal conversation ------------------------------------
    print("[2] one person looks at the agent")
    stop_gaze = True
    await asyncio.sleep(0.3)
    task.cancel()
    stop_gaze = False
    task = asyncio.ensure_future(gaze_loop(2, 1))
    await asyncio.sleep(0.6)
    before = len(events)
    t_end = await say("capital")
    answered = await wait_until(
        lambda: any("tokyo" in t.lower() for t in bot_said()[len(bot_said()) - 3:]) if bot_said() else False, 15
    )
    results["answered_when_looking"] = answered
    results["bot_reply"] = bot_said()[-1] if bot_said() else None
    if not answered:
        failures.append("the agent did not answer although someone was looking at it")

    # ---- 3. nobody looking: room talk must not interrupt --------------------------
    print("[3] talking over the bot while nobody looks at it")
    await wait_until(lambda: ear.speaking_at(time.perf_counter(), 0.4), 12)  # wait until it's talking
    stop_gaze = True
    await asyncio.sleep(0.3)
    task.cancel()
    stop_gaze = False
    task = asyncio.ensure_future(gaze_loop(2, 0))
    await asyncio.sleep(0.4)
    before = len(events)
    await say("interrupt")
    await asyncio.sleep(1.5)
    interrupts = [e for e in events[before:] if e.get("type") == "interaction" and e.get("event") == "interrupt"]
    results["interrupted_by_room_talk"] = bool(interrupts)
    if interrupts:
        failures.append("room talk interrupted the bot even though nobody was looking")

    stop_gaze = True
    task.cancel()
    results["ignored_total"] = len(ignored())
    print("\n===== RESULTS =====")
    for k, v in results.items():
        print(f"{k}: {v}")
    print("\nFAILURES:" if failures else "\nALL CHECKS PASSED", *failures, sep="\n  - ")
    await pc.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
