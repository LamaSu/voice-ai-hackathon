"""Minimal direct Gradium websocket clients (outside Pipecat).

Used by smoke tests and to generate audio fixtures. The protocol mirrors
pipecat.services.gradium.{stt,tts}.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid

import numpy as np
import websockets

TTS_URL = "wss://api.gradium.ai/api/speech/tts"
STT_URL = "wss://api.gradium.ai/api/speech/asr"
TTS_SAMPLE_RATE = 48000
DEFAULT_VOICE = "_6Aslh2DxfmnRLmP"


async def tts(text: str, api_key: str, voice: str | None = None) -> tuple[np.ndarray, int, float]:
    """Synthesize text. Returns (int16 pcm, sample_rate, time_to_first_audio_s)."""
    req = str(uuid.uuid4())
    headers = {"x-api-key": api_key}
    chunks: list[bytes] = []
    t0 = time.perf_counter()
    ttfa = -1.0
    async with websockets.connect(TTS_URL, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "output_format": "pcm",
                    "voice_id": voice or DEFAULT_VOICE,
                    "close_ws_on_eos": False,
                    "client_req_id": req,
                }
            )
        )
        await ws.send(json.dumps({"type": "text", "text": text, "client_req_id": req}))
        await ws.send(json.dumps({"type": "end_of_stream", "client_req_id": req}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if msg["type"] == "audio":
                if ttfa < 0:
                    ttfa = time.perf_counter() - t0
                chunks.append(base64.b64decode(msg["audio"]))
            elif msg["type"] == "end_of_stream":
                break
            elif msg["type"] == "error":
                raise RuntimeError(f"Gradium TTS error: {msg}")
    pcm = np.frombuffer(b"".join(chunks), dtype=np.int16)
    return pcm, TTS_SAMPLE_RATE, ttfa


def resample_int16(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return pcm
    n = int(len(pcm) * dst_rate / src_rate)
    x_old = np.linspace(0, 1, num=len(pcm), endpoint=False)
    x_new = np.linspace(0, 1, num=n, endpoint=False)
    return np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)


async def stt(
    pcm16k: np.ndarray, api_key: str, realtime: bool = False, language: str = "en"
) -> tuple[str, float]:
    """Transcribe 16 kHz int16 audio. Returns (text, seconds from last audio to final text)."""
    headers = {"x-api-key": api_key}
    words: list[str] = []
    chunk = int(0.08 * 16000)
    async with websockets.connect(STT_URL, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "model_name": "default",
                    "input_format": "pcm_16000",
                    "json_config": {"language": language},
                }
            )
        )
        ready = json.loads(await ws.recv())
        if ready["type"] != "ready":
            raise RuntimeError(f"Gradium STT setup failed: {ready}")

        async def sender():
            for i in range(0, len(pcm16k), chunk):
                data = pcm16k[i : i + chunk].tobytes()
                await ws.send(
                    json.dumps({"type": "audio", "audio": base64.b64encode(data).decode()})
                )
                if realtime:
                    await asyncio.sleep(0.08)
            await ws.send(json.dumps({"type": "flush", "flush_id": "1"}))
            return time.perf_counter()

        send_task = asyncio.create_task(sender())
        flushed_at = None
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3 if flushed_at else 20))
            except asyncio.TimeoutError:
                break
            if msg["type"] == "text":
                words.append(msg["text"])
            elif msg["type"] == "flushed":
                flushed_at = time.perf_counter()
            elif msg["type"] == "error":
                raise RuntimeError(f"Gradium STT error: {msg}")
        t_sent = await send_task
        latency = (flushed_at or time.perf_counter()) - t_sent
    return " ".join(words).strip(), latency
