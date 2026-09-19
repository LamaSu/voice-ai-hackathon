"""Provider factories: Gradium (STT/TTS) and General Compute (LLM). Keys come from .env."""

from __future__ import annotations

from typing import Any

from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transcriptions.language import Language

from app.config import Settings
from app.stt_parakeet import ParakeetSTTService

BOT_NAME = "Jev"

SYSTEM_PROMPT = """You are Jev, a friendly voice assistant in a live conversation.
Replies are spoken: 1-2 short sentences, no lists, no markdown, no emojis.
The system prompt names who is speaking; greet people by name when you know it.
If interrupted, don't repeat yourself — answer the interruption."""


def make_stt(s: Settings, parakeet_model: Any = None):
    """Local Parakeet when available (much lower first-word latency), else Gradium."""
    if s.stt_engine == "parakeet" and parakeet_model is not None:
        return ParakeetSTTService(model=parakeet_model, language=Language.EN)
    return GradiumSTTService(
        api_key=s.gradium_api_key,
        settings=GradiumSTTService.Settings(language=Language.EN, delay_in_frames=7),
    )


def make_tts(s: Settings) -> GradiumTTSService:
    settings = GradiumTTSService.Settings(voice=s.gradium_tts_voice) if s.gradium_tts_voice else None
    return GradiumTTSService(api_key=s.gradium_api_key, settings=settings)


def make_llm(s: Settings) -> OpenAILLMService:
    return OpenAILLMService(
        api_key=s.general_compute_api_key,
        base_url=s.general_compute_base_url,
        settings=OpenAILLMService.Settings(model=s.llm_model, temperature=0.6, max_tokens=220),
    )
