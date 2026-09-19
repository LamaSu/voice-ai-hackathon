"""BotTap: sits after transport.output() and records what the bot actually said (playback-synced)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.state.engine import StateEngine


class BotTap(FrameProcessor):
    def __init__(
        self,
        engine: StateEngine,
        on_response_done: Callable[[], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._engine = engine
        self._on_response_done = on_response_done

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        bot = self._engine.state.bot
        if isinstance(frame, LLMFullResponseStartFrame):
            bot.spoken_text = ""
            bot.current_sentence = ""
            bot.response_done = False
        elif isinstance(frame, TTSTextFrame):
            word = frame.text.strip()
            if word:
                if bot.current_sentence.endswith((".", "!", "?")):
                    bot.current_sentence = ""
                bot.current_sentence = (bot.current_sentence + " " + word).strip()
                bot.spoken_text = (bot.spoken_text + " " + word).strip()
                await self._engine.publish_snapshot()
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._on_response_done:
                await self._on_response_done()
        await self.push_frame(frame, direction)
