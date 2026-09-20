"""The demo moment, wired end to end: notice confusion, stop, ask.

These exist because `decide_probe`, `pick_probe` and `ProbeCooldown` were all
built and unit-tested, and then *nothing called them* — `probes.py` was dead
code in the running system and the agent never repaired anything. Unit tests on
a pure function cannot catch that; these check the controller actually fires.
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import InterruptionFrame, TTSSpeakFrame

from app.contracts import ProsodyDelta, UserState
from app.state.engine import StateEngine
from app.state.interaction_state import Phase
from app.turns.controller import InteractionController
from app.jev.client import JevResult


class SilentJev:
    async def ask(self, state, questions):
        return JevResult(ok=False, error="not used here")


def confused(confusion_p: float = 0.85, t_ms: int = 0) -> UserState:
    return UserState(
        t_ms=t_ms,
        au={"brow_lower": 0.5, "lip_press": 0.3},
        gaze_away=True,
        prosody_delta=ProsodyDelta(pause_ms=900, rate=-0.3),
        confusion_p=confusion_p,
    )


@pytest.fixture
def ctrl():
    engine = StateEngine()
    c = InteractionController(engine, SilentJev())
    c._engine = engine
    return c


def speaking(c, yes: bool = True) -> None:
    c.s.bot.speaking = yes
    c._engine.set_phase(Phase.BOT_SPEAKING if yes else Phase.IDLE)


async def drive(c, samples: int, p: float = 0.85):
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    c.push_frame = capture
    for i in range(samples):
        await c.observe_user_state(confused(p, t_ms=i * 100))
    return pushed


async def test_sustained_confusion_mid_explanation_stops_the_bot_and_asks(ctrl):
    speaking(ctrl)

    pushed = await drive(ctrl, samples=12)

    kinds = [type(f).__name__ for f in pushed]
    assert "InterruptionFrame" in kinds, "the bot must actually stop talking"
    assert "TTSSpeakFrame" in kinds, "a probe question must be spoken"
    # Stop first, then ask — otherwise we talk over ourselves.
    assert kinds.index("InterruptionFrame") < kinds.index("TTSSpeakFrame")


async def test_the_question_is_non_leading(ctrl):
    speaking(ctrl)

    pushed = await drive(ctrl, samples=12)
    question = next(f.text for f in pushed if isinstance(f, TTSSpeakFrame)).lower()

    # "Are you confused?" leads; "does that make sense?" gets a reflexive yes.
    assert "confus" not in question
    assert "make sense" not in question
    assert question.strip().endswith("?")


async def test_one_confused_frame_is_not_enough(ctrl):
    # confusion_p is a noisy prior, not a verdict.
    speaking(ctrl)

    pushed = await drive(ctrl, samples=1)

    assert not [f for f in pushed if isinstance(f, (InterruptionFrame, TTSSpeakFrame))]


async def test_a_calm_listener_is_never_interrupted(ctrl):
    speaking(ctrl)

    pushed = await drive(ctrl, samples=30, p=0.05)

    assert not pushed


async def test_silence_is_not_interrupted(ctrl):
    # Probing someone the agent is not currently explaining to is just rude.
    speaking(ctrl, False)

    pushed = await drive(ctrl, samples=30)

    assert not [f for f in pushed if isinstance(f, TTSSpeakFrame)]


async def test_it_does_not_probe_twice_over_one_explanation(ctrl):
    speaking(ctrl)
    await drive(ctrl, samples=12)

    pushed = await drive(ctrl, samples=12)

    assert not [f for f in pushed if isinstance(f, TTSSpeakFrame)]


async def test_the_probe_is_announced_so_the_ui_and_log_can_show_it(ctrl):
    seen: list[dict] = []
    async def collect(event):
        seen.append(event)

    ctrl._engine.add_publisher(collect)
    speaking(ctrl)

    await drive(ctrl, samples=12)

    probes = [e for e in seen if e.get("event") == "probe"]
    assert probes and probes[0]["question"]
    assert probes[0]["confusion_p"] >= 0.8


class OneProbeClip:
    """A library holding a pre-rendered clip for the generic probe only."""

    class _Clip:
        pcm = b"\x00\x00" * 4800
        sample_rate = 48000

    def available(self) -> bool:
        return True

    def probe(self, question: str):
        if question.strip().lower() == "which part should i go over again?":
            return self._Clip()
        return None


async def test_a_prerendered_probe_plays_instantly_instead_of_paying_tts(ctrl):
    # The repair is the demo's moment; synthesising it would put ~0.4-1s of
    # silence between noticing and saying so.
    ctrl._fillers = OneProbeClip()
    speaking(ctrl)

    pushed = await drive(ctrl, samples=12)
    kinds = [type(f).__name__ for f in pushed]

    assert "SpeechOutputAudioRawFrame" in kinds
    assert "TTSSpeakFrame" not in kinds


async def test_it_falls_back_to_synthesis_when_no_clip_exists(ctrl):
    class NoClips(OneProbeClip):
        def probe(self, question: str):
            return None

    ctrl._fillers = NoClips()
    speaking(ctrl)

    pushed = await drive(ctrl, samples=12)

    assert [f for f in pushed if isinstance(f, TTSSpeakFrame)]
