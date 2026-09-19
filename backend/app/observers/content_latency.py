"""Time from end of speech to the first *answer* audio, distinct from first sound.

Pipecat's `UserBotLatencyObserver` stops at `BotStartedSpeakingFrame`. Once a
cached filler can be the first thing played, that frame marks the filler, and
the headline number silently changes meaning: it becomes "when did the agent
make a noise", not "when did it answer".

This watches for the first `TTSAudioRawFrame` of a turn instead. A filler is a
plain `SpeechOutputAudioRawFrame`, pushed by the controller; only the TTS
service emits `TTSAudioRawFrame`. So the two are cleanly distinguishable
without the observer needing to know fillers exist.

With no filler configured the two numbers are equal, which is the honest
answer in that case too.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pipecat.frames.frames import (
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed


class ContentLatencyObserver(BaseObserver):
    """Measures end of speech → first synthesized answer audio."""

    def __init__(self, *, time_source: Callable[[], float] = time.time, **kwargs):
        super().__init__(**kwargs)
        self._time = time_source
        self._speech_ended_at: float | None = None
        self._armed = False
        self._latest_ms: float | None = None

    @property
    def latest_ms(self) -> float | None:
        """Most recent end-of-speech to first-answer-audio, in ms."""
        return self._latest_ms

    def take(self) -> float | None:
        """Read and clear the pending measurement, so a turn is reported once."""
        value, self._latest_ms = self._latest_ms, None
        return value

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # A new turn starts measuring; an interruption re-arms it, which is
            # what we want — the replanned answer is the one worth timing.
            self._speech_ended_at = self._time()
            self._armed = True
            return

        if self._armed and isinstance(frame, TTSAudioRawFrame):
            if self._speech_ended_at is not None:
                self._latest_ms = round((self._time() - self._speech_ended_at) * 1000, 1)
            self._armed = False
