"""General Compute LLM, with gpt-oss's chain-of-thought kept out of the agent's mouth.

gpt-oss is a reasoning model. Its thinking normally arrives in a separate `reasoning` field
on the `analysis` channel, but the provider occasionally flushes that text into `content`
instead — and Pipecat speaks any content it sees, so the agent reads its own notes aloud
("We need to respond appropriately. The user says...").

Two layers, because the leak is intermittent and comes from the provider:
1. `reasoning_effort="low"` — the least thinking the API allows (it rejects "none").
2. `ReasoningFilter` — watches the start of each spoken response and drops it if it opens
   like analysis rather than speech.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService

# How the model talks to itself. These are openings, not substrings found mid-sentence.
_ANALYSIS_OPENING = re.compile(
    r"^\s*(…|\.\.\.)?\s*("
    r"we (need|should|must|have) to\b"
    r"|we (should|can|could|will|might) (respond|answer|reply|keep|say|note|acknowledge)\b"
    r"|the user (says|asks|wants|is)\b"
    r"|user (says|asks|wants)\b"
    r"|let'?s (respond|answer|keep|note)\b"
    r"|probably (a |the )?(reacting|responding|asking)\b"
    r"|we can respond\b"
    r"|note:? we\b"
    r"|as jev,? (a|the)\b"
    r")",
    re.IGNORECASE,
)
SNIFF_CHARS = 28  # enough to recognise an opening, short enough not to delay speech


def looks_like_reasoning(text: str) -> bool:
    """True when a spoken response opens like the model's private notes."""
    return bool(_ANALYSIS_OPENING.match(text))


class _FinalChannelOnly:
    """Wraps the completion stream and hides non-final channels from the pipeline."""

    def __init__(self, stream: Any):
        self._stream = stream

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        async for chunk in self._stream:
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = choices[0].delta
                extra = getattr(delta, "model_extra", None) or {}
                channel = extra.get("channel")
                # analysis / commentary carry the model's thinking, never its answer
                if channel and channel != "final" and getattr(delta, "content", None):
                    if not getattr(delta, "tool_calls", None) and not getattr(chunk, "usage", None):
                        continue
            yield chunk

    async def close(self) -> None:
        for name in ("close", "aclose"):
            fn = getattr(self._stream, name, None)
            if fn:
                await fn()
                return

    def __getattr__(self, item: str) -> Any:
        return getattr(self._stream, item)


class GeneralComputeLLMService(OpenAILLMService):
    """OpenAI-compatible, with reasoning minimised and the analysis channel filtered out."""

    def build_chat_completion_params(self, params_from_context) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        # The API accepts low|medium|high — "none" is rejected — so low is as close to
        # no thinking as it allows.
        params.setdefault("reasoning_effort", "low")
        return params

    async def get_chat_completions(self, context):
        return _FinalChannelOnly(await super().get_chat_completions(context))


class ReasoningFilter(FrameProcessor):
    """Drops a spoken response that opens like chain-of-thought.

    Sits between the LLM and the TTS. It holds the first few characters of each response,
    decides once, then either releases them and passes the rest through, or swallows the
    whole response so the agent stays quiet instead of narrating its notes.
    """

    def __init__(self, on_suppressed=None, **kwargs):
        super().__init__(**kwargs)
        self._buffer: list[str] = []
        self._deciding = False
        self._suppressing = False
        self._on_suppressed = on_suppressed

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
            self._deciding = True
            self._suppressing = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._suppressing:
                return
            if self._deciding:
                self._buffer.append(frame.text)
                joined = "".join(self._buffer)
                if len(joined) < SNIFF_CHARS and "\n" not in joined:
                    return  # keep holding: too early to tell
                self._deciding = False
                if looks_like_reasoning(joined):
                    self._suppressing = True
                    logger.warning(f"suppressed reasoning leak: {joined[:60]!r}")
                    if self._on_suppressed:
                        await self._on_suppressed(joined)
                    return
                await self.push_frame(LLMTextFrame("".join(self._buffer)), direction)
                self._buffer = []
                return
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._deciding and self._buffer:
                # response ended inside the sniff window: decide on what we have
                joined = "".join(self._buffer)
                if looks_like_reasoning(joined):
                    logger.warning(f"suppressed reasoning leak: {joined[:60]!r}")
                    if self._on_suppressed:
                        await self._on_suppressed(joined)
                else:
                    await self.push_frame(LLMTextFrame(joined), direction)
            self._buffer = []
            self._deciding = False
            self._suppressing = False

        await self.push_frame(frame, direction)
