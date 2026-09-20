"""Pre-generate paralinguistic filler utterances with Gradium TTS and cache them locally.

These are played the moment a user turn ends, while the LLM is still thinking, so the
agent sounds like it's reacting instead of going silent for a second. Jev picks the
category (or none) as part of the end-of-turn question set.

Usage: uv run python scripts/make_fillers.py [--voice <id>] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.providers import gradium_raw  # noqa: E402

OUT = BACKEND_DIR / "assets" / "fillers"

# Categories are what Jev chooses between; the wording inside one is interchangeable.
CATALOG: dict[str, list[str]] = {
    "thinking": [
        "Hmm.",
        "Let me think about that.",
        "Let me think.",
        "Give me a sec.",
        "Hang on.",
        "Hold on.",
        "Let's see...",
        "Okay, so...",
        "Well...",
        "Um...",
    ],
    "acknowledging": [
        "That's interesting.",
        "Huh.",
        "Oh, wow.",
        "Yeah...",
        "Right, right.",
        "I see.",
        "Got it.",
        "Fair enough.",
        "Mm-hm.",
        "Okay.",
    ],
    "weighing": [
        "That's a good question.",
        "Good point.",
        "Interesting question.",
        "That's a tough one.",
        "I hadn't thought of it that way.",
        "That's fair.",
        "It's funny you ask.",
        "Now that you mention it...",
    ],
    "reframing": [
        "So what you're saying is...",
        "If I'm hearing you right...",
        "So basically...",
        "In other words...",
        "You mean...?",
    ],
    "nuance": [
        "I mean...",
        "Well, here's the thing...",
        "The thing is...",
        "To be honest...",
        "I don't know...",
        "It depends.",
        "Sort of, yeah.",
        "Yes and no.",
    ],
    "casual": [
        "Man...",
        "Oof.",
        "Ooh.",
        "Dang.",
        "Welp.",
        "Ah.",
        "Look...",
        "Listen...",
    ],
}


def slug(text: str) -> str:
    keep = "".join(c.lower() if c.isalnum() else "-" for c in text)
    return "-".join(p for p in keep.split("-") if p)[:40]


def write_wav(path: Path, pcm, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


async def main() -> None:
    # The repair questions are a fixed list, so cache them like fillers: paying
    # TTS at the demo's most important moment would put ~0.4-1s of silence
    # between noticing the listener is lost and saying so.
    from app.turns.probes import PROBE_CANDIDATES

    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=None, help="Gradium voice id (default: the bot's voice)")
    ap.add_argument("--force", action="store_true", help="regenerate clips that already exist")
    args = ap.parse_args()

    s = get_settings()
    voice = args.voice or s.gradium_tts_voice or gradium_raw.DEFAULT_VOICE
    OUT.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"voice": voice, "sample_rate": gradium_raw.TTS_SAMPLE_RATE, "clips": []}
    catalog = dict(CATALOG)
    catalog["probe"] = tuple(c.question for c in PROBE_CANDIDATES)
    for category, phrases in catalog.items():
        for text in phrases:
            name = f"{category}--{slug(text)}.wav"
            path = OUT / name
            if path.exists() and not args.force:
                with wave.open(str(path)) as w:
                    dur = w.getnframes() / w.getframerate()
            else:
                pcm, sr, _ = await gradium_raw.tts(text, s.gradium_api_key, voice)
                write_wav(path, pcm, sr)
                dur = len(pcm) / sr
                print(f"{category:14} {text!r:40} {dur:.2f}s -> {name}")
            manifest["clips"].append(
                {"category": category, "text": text, "file": name, "duration_s": round(dur, 2)}
            )
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    total = sum(c["duration_s"] for c in manifest["clips"])
    print(f"\n{len(manifest['clips'])} clips, {total:.1f}s of audio, voice {voice} -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
