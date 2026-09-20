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
