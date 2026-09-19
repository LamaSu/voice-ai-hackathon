"""Provider factories: Gradium (STT/TTS) and General Compute (LLM). Keys come from .env."""

from __future__ import annotations

from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transcriptions.language import Language

from app.config import Settings

SYSTEM_PROMPT = """You are Jev, a friendly, quick-witted voice assistant in a live spoken conversation.
- Your replies are spoken aloud: keep them short (1-3 sentences), natural, no lists, no markdown, no emojis.
- User messages may start with a speaker tag like [Akash] or [S2]; several people may be talking. Address people by name when you know it.
- If you get interrupted, don't repeat what you already said; respond to the interruption.
- When someone introduces themselves, greet them by name and remember it."""


def make_stt(s: Settings) -> GradiumSTTService:
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
