"""Keep the model's thinking out of the agent's mouth.

`gpt-oss-120b` is a reasoning model. Over an OpenAI-compatible API it streams
its chain of thought inline, wrapped in `<think>` tags — Pipecat's own Groq
service documents exactly this for the GPT-OSS family. Nothing downstream
strips it, so the reasoning reaches TTS and gets spoken aloud.

This filter sits on the TTS service and drops anything between a start and end
marker. It has to be stateful: text arrives in streaming chunks and a tag is
regularly split across two of them ("<thi" then "nk>"), so a per-chunk regex
would miss it and leak the very thing we are removing.

Unterminated reasoning is treated as reasoning — if the model opens a block and
never closes it, staying silent is better than narrating. The state resets on
interruption so one bad turn cannot mute the rest of the session.
"""

from __future__ import annotations

from loguru import logger
from pipecat.utils.text.base_text_filter import BaseTextFilter

# Markers seen from GPT-OSS / harmony-style models over OpenAI-compatible APIs.
DEFAULT_MARKERS: tuple[tuple[str, str], ...] = (
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<|channel|>analysis<|message|>", "<|end|>"),
    ("<reasoning>", "</reasoning>"),
)


class ReasoningFilter(BaseTextFilter):
    """Strips reasoning spans from streamed LLM text before it reaches TTS."""

    def __init__(self, markers: tuple[tuple[str, str], ...] = DEFAULT_MARKERS):
        self._markers = markers
        self._max_start = max(len(start) for start, _ in markers)
        self._reset()

    def _reset(self) -> None:
        self._inside: tuple[str, str] | None = None
        self._pending = ""  # a possible tag prefix straddling two chunks
        self._suppressed = 0

    async def filter(self, text: str) -> str:
        """Return `text` with reasoning spans removed."""
        buf = self._pending + text
        self._pending = ""
        out: list[str] = []

        while buf:
            if self._inside:
                _, end = self._inside
                idx = buf.find(end)
                if idx == -1:
                    # Still inside. Hold back only enough to catch a split end tag.
                    keep = len(end) - 1
                    self._suppressed += max(0, len(buf) - keep)
                    self._pending = buf[-keep:] if keep else ""
                    buf = ""
                    continue
                self._suppressed += idx
                buf = buf[idx + len(end) :]
                self._inside = None
                continue

            # Not inside: find the earliest start marker.
            hit_at, hit = len(buf), None
            for start, end in self._markers:
                i = buf.find(start)
                if i != -1 and i < hit_at:
                    hit_at, hit = i, (start, end)

            if hit:
                out.append(buf[:hit_at])
                buf = buf[hit_at + len(hit[0]) :]
                self._inside = hit
                continue

            # No complete start marker. A trailing partial one must be held back,
            # or "<thi" would be spoken before "nk>" arrives in the next chunk.
            tail = self._partial_tail(buf)
            if tail:
                out.append(buf[:-tail])
                self._pending = buf[-tail:]
            else:
                out.append(buf)
            buf = ""

        return "".join(out)

    def _partial_tail(self, buf: str) -> int:
        """Length of a trailing substring that could still become a start marker."""
        for n in range(min(self._max_start - 1, len(buf)), 0, -1):
            tail = buf[-n:]
            if any(start.startswith(tail) for start, _ in self._markers):
                return n
        return 0

    async def handle_interruption(self) -> None:
        """A barge-in abandons the response; drop any half-seen reasoning."""
        if self._suppressed:
            logger.debug(f"reasoning filter: suppressed {self._suppressed} chars this turn")
        self._reset()

    async def reset_interruption(self) -> None:
        self._reset()

    async def update_settings(self, settings) -> None:  # noqa: ANN001 - base signature
        return
