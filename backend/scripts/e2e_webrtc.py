"""Headless end-to-end test against the running server over real WebRTC (aiortc).

Plays Gradium-generated "user" clips into the bot, listens to the bot's audio, collects the
RTVI server-message events (jev decisions, turn events, memory) and asserts behaviour:

  1. intro          -> end_of_turn RESPOND, bot answers, memory binds the name "Priya"
  2. story request  -> bot starts a story
  3. "Yeah." during bot speech        -> Jev overlap=backchannel, bot KEEPS talking
  4. "Actually ... a cat instead?"    -> Jev overlap=interrupt, bot audio STOPS, new answer
  5. second voice "I'm Marcus ..."    -> a new speaker label (ECAPA) and bound name

Usage:
  uv run python -m app.server &        # in another terminal
  uv run python scripts/e2e_webrtc.py [--url http://127.0.0.1:7860]
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import json
import sys
import time
import uuid
import wave
from pathlib import Path

import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SR_OUT = 48000
FRAME_S = 0.02
BOT_RMS_THRESHOLD = 0.01


def load_clip(name: str) -> np.ndarray:
    with wave.open(str(FIXTURES / f"{name}.wav")) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    # 16k -> 48k for the WebRTC track
    x_new = np.arange(0, len(pcm) * 3) / 3.0
    return np.interp(x_new, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


class MicTrack(MediaStreamTrack):
    """Realtime-paced microphone: silence unless a clip is queued."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._queue = np.zeros(0, dtype=np.int16)
        self._pts = 0
        self._start: float | None = None
        self.clip_done = asyncio.Event()

    def play(self, pcm48: np.ndarray) -> None:
        self.clip_done.clear()
        self._queue = np.concatenate([self._queue, pcm48])

    async def recv(self) -> AudioFrame:
        n = int(SR_OUT * FRAME_S)
        if self._start is None:
            self._start = time.perf_counter()
        target = self._start + self._pts / SR_OUT
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        if len(self._queue) >= n:
            chunk, self._queue = self._queue[:n], self._queue[n:]
            if len(self._queue) == 0:
                self.clip_done.set()
        else:
            chunk = np.zeros(n, dtype=np.int16)
            if len(self._queue):
                chunk[: len(self._queue)] = self._queue
                self._queue = np.zeros(0, dtype=np.int16)
                self.clip_done.set()
        frame = AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = SR_OUT
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SR_OUT)
        self._pts += n
        return frame


class BotEar:
    """Tracks when the bot's audio is audible."""

    def __init__(self):
        self.samples: list[tuple[float, float]] = []  # (t, rms)

    async def consume(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            a = frame.to_ndarray().astype(np.float32).reshape(-1) / 32768.0
            self.samples.append((time.perf_counter(), float(np.sqrt(np.mean(a * a) + 1e-12))))

    def speaking_at(self, t: float, window: float = 0.3) -> bool:
        return any(rms > BOT_RMS_THRESHOLD for ts, rms in self.samples if t - window <= ts <= t)

    def first_audio_after(self, t: float) -> float | None:
        for ts, rms in self.samples:
            if ts >= t and rms > BOT_RMS_THRESHOLD:
                return ts
        return None

    def silent_since(self, t: float, hold: float = 0.4) -> float | None:
        """First time >= t after which the bot stays silent for `hold` seconds."""
        pts = [(ts, rms) for ts, rms in self.samples if ts >= t]
        for i, (ts, _) in enumerate(pts):
            window = [r for tt, r in pts[i:] if tt <= ts + hold]
            if window and all(r <= BOT_RMS_THRESHOLD for r in window) and pts[-1][0] >= ts + hold:
                return ts
        return None


async def wait_until(pred, timeout: float, step: float = 0.05) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7860")
    ap.add_argument("--keep-memory", action="store_true", help="don't wipe people memory first")
    args = ap.parse_args()

    events: list[dict] = []
    pc = RTCPeerConnection()
    mic = MicTrack()
    pc.addTrack(mic)
    ear = BotEar()
    dc = pc.createDataChannel("chat")
    dc_open = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            asyncio.ensure_future(ear.consume(track))

    @dc.on("open")
    def on_open():
        dc_open.set()

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
        r = await http.post(
            f"{args.url}/api/offer",
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        )
        r.raise_for_status()
        ans = r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    await asyncio.wait_for(dc_open.wait(), 15)
    dc.send(json.dumps({
        "label": "rtvi-ai", "type": "client-ready", "id": str(uuid.uuid4()),
        "data": {"version": "1.0.0", "about": {"library": "e2e-aiortc"}},
    }))
    if not args.keep_memory:
        dc.send(json.dumps({
            "label": "rtvi-ai", "type": "client-message", "id": str(uuid.uuid4()),
            "data": {"t": "reset_memory", "d": {}},
        }))
    print("connected; warming up...")
    await asyncio.sleep(3.0)

    results: dict[str, object] = {}
    failures: list[str] = []

    def evs(type_: str, **match):
        return [e for e in events if e.get("type") == type_ and all(e.get(k) == v for k, v in match.items())]

    async def say(name: str) -> tuple[float, float]:
        clip = load_clip(name)
        t0 = time.perf_counter()
        mic.play(clip)
        await mic.clip_done.wait()
        return t0, time.perf_counter()

    async def wait_bot_done(timeout=25.0):
        await asyncio.sleep(0.5)
        await wait_until(lambda: ear.silent_since(time.perf_counter() - 1.3, hold=1.2) is not None, timeout)

    # ---- 1. intro --------------------------------------------------------------
    print("\n[1] intro")
    _, t_end = await say("intro")
    ok = await wait_until(lambda: ear.first_audio_after(t_end) is not None, 12)
    if ok:
        lat = ear.first_audio_after(t_end) - t_end
        results["intro_end_of_speech_to_first_audio_s"] = round(lat, 2)
        print(f"    bot started {lat:.2f}s after end of user speech")
    else:
        failures.append("bot never answered the intro")
    await wait_bot_done()
    named = await wait_until(
        lambda: any(p.get("name") == "Priya" for e in evs("memory") for p in e.get("people", [])), 8
    )
    results["memory_bound_priya"] = named
    if not named:
        failures.append("memory did not bind the name Priya")

    # ---- 2+3. story, backchannel ----------------------------------------------------
    print("[2] story request")
    _, t_end = await say("story")
    ok = await wait_until(lambda: ear.first_audio_after(t_end) is not None, 12)
    if not ok:
        failures.append("bot never started the story")
    else:
        t_bot = ear.first_audio_after(t_end)
        results["story_end_of_speech_to_first_audio_s"] = round(t_bot - t_end, 2)
        await asyncio.sleep(max(0.0, t_bot + 1.0 - time.perf_counter()))
        print("[3] backchannel 'Yeah.' while bot talks")
        n_before = len(events)
        t_bc0, t_bc1 = await say("yeah")
        await asyncio.sleep(1.2)
        still = ear.speaking_at(time.perf_counter(), window=0.6)
        bc_events = [e for e in events[n_before:] if e.get("type") == "interaction"]
        results["backchannel_bot_kept_talking"] = still
        results["backchannel_turn_events"] = [e.get("event") for e in bc_events]
        if any(e.get("event") == "interrupt" for e in bc_events):
            failures.append("backchannel caused an interrupt")
        if not still:
            failures.append("bot stopped talking after the backchannel (may just have finished)")

        # ---- 4. interruption ----------------------------------------------------
        if ear.speaking_at(time.perf_counter(), window=0.4):
            print("[4] barge-in 'Actually, can you make it about a cat instead?'")
            n_before = len(events)
            t_i0, t_i1 = await say("interrupt")

            def int_events():
                return [e for e in events[n_before:] if e.get("type") == "interaction" and e.get("event") == "interrupt"]

            await wait_until(lambda: bool(int_events()), 4.0)  # tolerate provider jitter
            await asyncio.sleep(0.5)
            stopped_at = ear.silent_since(t_i0, hold=0.4)
            int_events = int_events()
            if int_events:
                results["interrupt_decision_after_speech_start_s"] = round(int_events[0]["_rx"] - t_i0, 2)
                results["interrupt_reason"] = int_events[0].get("reason")
            else:
                failures.append("no interrupt event")
            if stopped_at:
                results["bot_audio_stopped_after_speech_start_s"] = round(stopped_at - t_i0, 2)
            else:
                failures.append("bot audio did not stop")
            ok = await wait_until(lambda: ear.first_audio_after(t_i1 + 0.2) is not None, 12)
            if ok:
                results["interrupt_end_of_speech_to_new_answer_s"] = round(ear.first_audio_after(t_i1 + 0.2) - t_i1, 2)
            else:
                failures.append("bot did not answer after the interruption")
        else:
            failures.append("bot finished story before interrupt test could run")
    await wait_bot_done()

    # ---- 5. second speaker -------------------------------------------------------
    print("[5] second speaker (different voice)")
    await say("second_person")
    await wait_bot_done()
    await asyncio.sleep(2.0)
    # the name can arrive from the memory pass after the exchange, so give it a moment
    await wait_until(
        lambda: any(
            p.get("name") and p["label"] != "S1" for e in evs("memory") for p in e.get("people", [])
        ),
        12,
    )
    labels = {e["state"]["speaker"]["label"] for e in evs("state") if e["state"]["speaker"]["label"]}
    results["speaker_labels_seen"] = sorted(labels)
    mem = evs("memory")
    people = mem[-1]["people"] if mem else []
    results["people"] = [(p["label"], p["name"]) for p in people]
    if len(labels) < 2:
        failures.append("ECAPA did not separate the two voices")
    # the ASR may hear "Marcus" as "Mark"; what matters is that the new voice got a name
    second = [p for p in people if p["label"] != "S1"]
    if not any(p.get("name") for p in second):
        failures.append(f"memory did not bind a name for the second speaker: {people}")

    # ---- summary -----------------------------------------------------------------
    fillers = [e for e in events if e.get("type") == "interaction" and e.get("event") == "filler"]
    results["fillers_played"] = [(f["category"], f["text"], f["duration_s"]) for f in fillers]
    # the RTVI observer reports every push, so a processor that re-emits text can double
    # every line in the transcript: guard against that regression
    bot_lines = [e.get("text", "") for e in evs("transcript") if e.get("role") == "bot"]
    dupes = [a for a, b in zip(bot_lines, bot_lines[1:]) if a and a == b]
    results["duplicate_bot_lines"] = dupes
    if dupes:
        failures.append(f"bot transcript repeated: {dupes[0][:40]!r}")
    jev = evs("jev")
    lat = sorted(e["answers"]["latency_ms"] for e in jev if e.get("answers") and e["answers"].get("ok"))
    results["jev_calls"] = len(jev)
    if lat:
        results["jev_latency_ms_p50"] = lat[len(lat) // 2]
    results["jev_decisions"] = [(e["set"], e["decision"]["action"], e["decision"]["reason"], e.get("text", "")[:40]) for e in jev]
    results["transcripts"] = [(e.get("role"), e.get("speaker"), e.get("text", "")[:80]) for e in evs("transcript") if e.get("role") in ("user", "bot")]
    if mem:
        results["summary"] = mem[-1].get("summary")

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
    if not args.keep_memory:
        # don't leave the test's fake people (Priya, Marcus) in the real memory
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(f"{args.url}/api/memory/reset")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
