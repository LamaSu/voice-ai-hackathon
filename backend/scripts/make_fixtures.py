"""Generate user-voice WAV fixtures with Gradium TTS (a different voice than the bot).

Usage: uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.providers import gradium_raw  # noqa: E402

USER_VOICE = "YTpq7expH9539ERJ"
SECOND_VOICE = "LFZvm12tW_z0xfGo"
FIXTURES = BACKEND_DIR / "tests" / "fixtures"

CLIPS = {
    "intro": (USER_VOICE, "Hi there, my name is Priya."),
    "story": (USER_VOICE, "Can you tell me a story about a dragon who loves rainy Saturdays?"),
    "yeah": (USER_VOICE, "Yeah."),
    "interrupt": (USER_VOICE, "Actually, can you make it about a cat instead?"),
    "incomplete": (USER_VOICE, "I want to book a table for"),
    "complete_rest": (USER_VOICE, "two people at seven tonight."),
    "second_person": (SECOND_VOICE, "Hello, I'm Marcus. What's the capital of Japan?"),
    # reliably makes Jev pick a filler (a question worth weighing), for latency A/B
    "why_purr": (USER_VOICE, "Why do cats purr, actually? Is it always contentment?"),
}


def write_wav(path: Path, pcm16k) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16k.tobytes())


async def main() -> None:
    s = get_settings()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, (voice, text) in CLIPS.items():
        path = FIXTURES / f"{name}.wav"
        if path.exists():
            continue
        pcm, sr, _ = await gradium_raw.tts(text, s.gradium_api_key, voice)
        write_wav(path, gradium_raw.resample_int16(pcm, sr, 16000))
        print(f"{path.name}: {len(pcm) / sr:.2f}s  {text!r}")


if __name__ == "__main__":
    asyncio.run(main())
