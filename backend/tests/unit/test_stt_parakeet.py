"""ParakeetSTTService behaviour with a fake MLX model (no weights, no GPU).

The rules that matter: only VAD-confirmed speech is transcribed (on silence the model
invents fillers, and one hallucinated word can trigger a barge-in), the pre-roll before
the VAD fires is kept, and each utterance gets a fresh decoding context.
"""

from __future__ import annotations

import numpy as np
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.stt_parakeet import ParakeetSTTService

SR = 16000


class FakeStream:
    """Stands in for parakeet_mlx.StreamingParakeet: counts the audio it is fed."""

    def __init__(self, model):
        self.model = model
        self.samples = 0

    def __enter__(self):
        self.model.streams.append(self)
        return self

    def __exit__(self, *a):
        return False

    def add_audio(self, audio):
        self.samples += len(audio)

    @property
    def result(self):
        # text grows with the audio fed, so tests can assert on "did it transcribe this?"
        return type("R", (), {"text": f"words({self.samples})"})()


class FakeModel:
    def __init__(self):
        self.streams: list[FakeStream] = []

    def transcribe_stream(self, context_size=None):
        return FakeStream(self)


def audio(seconds: float) -> InputAudioRawFrame:
    return InputAudioRawFrame(
        audio=np.zeros(int(seconds * SR), dtype=np.int16).tobytes(), sample_rate=SR, num_channels=1
    )


def make():
    model = FakeModel()
    return ParakeetSTTService(model=model, sample_rate=SR), model


async def test_silence_outside_speech_is_never_transcribed():
    stt, model = make()
    down, _ = await run_test(
        stt,
        frames_to_send=[audio(0.25), audio(0.25), audio(0.25), SleepFrame(0.3)],
    )
    assert not [f for f in down if isinstance(f, (InterimTranscriptionFrame, TranscriptionFrame))]
    # only the warm-up stream exists, and nothing beyond the warm-up audio was fed
    assert sum(s.samples for s in model.streams) == int(0.5 * SR)


async def test_speech_is_transcribed_with_preroll_and_finalized_once():
    stt, model = make()
    down, _ = await run_test(
        stt,
        frames_to_send=[
            audio(0.3),  # before the VAD fires: kept as pre-roll
            VADUserStartedSpeakingFrame(),
            audio(0.5),
            SleepFrame(0.3),
            VADUserStoppedSpeakingFrame(),
            SleepFrame(0.3),
        ],
    )
    interims = [f for f in down if isinstance(f, InterimTranscriptionFrame)]
    finals = [f for f in down if isinstance(f, TranscriptionFrame)]
    assert interims, "expected interim transcripts while the user speaks"
    assert len(finals) == 1, "expected exactly one final transcript per utterance"
    # pre-roll (0.3s) + speech (0.5s) all reached the model, plus the flush padding
    fed = max(s.samples for s in model.streams)
    assert fed >= int(0.8 * SR)


async def test_each_utterance_gets_a_fresh_context():
    stt, model = make()
    await run_test(
        stt,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            audio(0.5),
            SleepFrame(0.2),
            VADUserStoppedSpeakingFrame(),
            SleepFrame(0.3),
            VADUserStartedSpeakingFrame(),
            audio(0.5),
            SleepFrame(0.2),
            VADUserStoppedSpeakingFrame(),
            SleepFrame(0.3),
        ],
    )
    used = [s for s in model.streams if s.samples > 0]
    assert len(used) >= 3, "warm-up plus one stream per utterance: no context carried over"
