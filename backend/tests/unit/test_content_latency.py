"""ContentLatencyObserver — first answer audio, not first sound."""

from __future__ import annotations

from pipecat.frames.frames import (
    OutputAudioRawFrame,
    SpeechOutputAudioRawFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)

from app.observers.content_latency import ContentLatencyObserver


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


class Pushed:
    """Minimal stand-in for FramePushed; the observer only reads .frame."""

    def __init__(self, frame):
        self.frame = frame


def silence(n: int = 320) -> bytes:
    return b"\x00" * n


async def test_measures_end_of_speech_to_first_tts_audio():
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(1.8)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.latest_ms == 1800.0


async def test_a_filler_does_not_stop_the_clock():
    # The whole reason this observer exists: a SpeechOutputAudioRawFrame is the
    # filler, and must not be mistaken for the answer.
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(0.4)
    await obs.on_push_frame(Pushed(SpeechOutputAudioRawFrame(silence(), 48000, 1)))
    clock.advance(1.7)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.latest_ms == 2100.0


async def test_plain_output_audio_is_also_ignored():
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(0.3)
    await obs.on_push_frame(Pushed(OutputAudioRawFrame(silence(), 48000, 1)))
    clock.advance(1.0)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.latest_ms == 1300.0


async def test_only_the_first_answer_frame_of_a_turn_counts():
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(1.2)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))
    clock.advance(5.0)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.latest_ms == 1200.0


async def test_take_reports_a_turn_once():
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(0.9)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.take() == 900.0
    assert obs.take() is None


async def test_an_interruption_re_arms_for_the_replanned_answer():
    clock = FakeClock()
    obs = ContentLatencyObserver(time_source=clock)

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(1.0)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))
    obs.take()

    await obs.on_push_frame(Pushed(VADUserStoppedSpeakingFrame()))
    clock.advance(0.7)
    await obs.on_push_frame(Pushed(TTSAudioRawFrame(silence(), 24000, 1)))

    assert obs.latest_ms == 700.0
