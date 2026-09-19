"""D2 — the three things the demo has to survive, as deterministic scenarios.

The live demo is scripted in COORDINATION.md: explain, get confused, repair,
barge in. These tests drive the same decision path the live pipeline uses,
without a microphone, a network call or a Gradium credit, so a regression
shows up in CI rather than in front of judges.

What they cover:

* **Bad audio** — noise and echo must not steal the floor, and the whole
  decision path must stay sane when Jev is unreachable.
* **Interruption** — a real barge-in stops the bot; a backchannel does not.
* **Staged confusion** — a `user_state` sample crossing the confusion
  threshold reaches the reasoner, and the turn that follows is measured.

Run with ``uv run pytest tests/e2e``. Nothing here spends credits.
"""

from __future__ import annotations

import pytest

from app.contracts import Metrics, ProsodyDelta, UserState
from app.jev.client import JevResult
from app.observers.latency_hud import LatencyHUD
from app.state.engine import StateEngine
from app.state.interaction_state import Phase
from app.turns.policy import Action, ConfusionTracker, decide_end_of_turn, decide_overlap, decide_probe


def overlap(intent, conf, **nouls):
    base = {"wants_floor": 0.2, "correcting_agent": 0.1, "addressed_to_agent": 0.8}
    base.update(nouls)
    return JevResult(
        nouls=base,
        choices={"intent": {"choice": intent, "confidence": conf, "probabilities": {}}},
    )


def end_of_turn(nxt, conf, complete, **nouls):
    base = {"turn_complete": complete, "addressed_to_agent": 0.9}
    base.update(nouls)
    return JevResult(
        nouls=base,
        choices={"next": {"choice": nxt, "confidence": conf, "probabilities": {}}},
    )


# --- Bad audio -------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,text,speech_s",
    [
        ("noise", "uh", 0.2),
        ("echo", "the answer is forty two", 0.9),
        ("side_talk", "can you pass the cable", 1.0),
    ],
)
def test_bad_audio_never_steals_the_floor(intent, text, speech_s):
    decision = decide_overlap(overlap(intent, 0.8, addressed_to_agent=0.1), text=text, speech_s=speech_s)

    assert decision.action is Action.CONTINUE


def test_room_noise_does_not_end_a_turn_that_has_no_words():
    decision = decide_end_of_turn(None, text="   ", silence_s=1.2)

    assert decision.action is Action.HOLD
    assert decision.reason == "no_text_yet"


def test_agent_still_answers_when_jev_is_unreachable():
    # Venue wifi is the most likely failure on the day. Losing Jev must
    # degrade to deterministic endpointing, never to a silent agent.
    timed_out = JevResult(ok=False, error="timeout")

    decision = decide_end_of_turn(timed_out, text="so how does that work?", silence_s=0.9)

    assert decision.action is Action.RESPOND
    assert decision.reason == "fallback_punctuation_or_silence"


def test_a_stalled_turn_is_released_rather_than_hanging():
    decision = decide_end_of_turn(
        end_of_turn("wait_for_more", 0.9, complete=0.1), text="I think that", silence_s=2.5
    )

    assert decision.action is Action.RESPOND
    assert decision.reason == "hold_timeout"


# --- Interruption ----------------------------------------------------------


def test_barge_in_stops_the_bot():
    decision = decide_overlap(
        overlap("interrupt", 0.9, wants_floor=0.9), text="no wait, go back", speech_s=0.6
    )

    assert decision.action is Action.INTERRUPT


def test_hard_stop_beats_a_confident_backchannel_reading():
    # "stop" must win even when Jev is 95% sure it was an mm-hm. Getting this
    # wrong on stage is the single most visible failure mode.
    decision = decide_overlap(overlap("backchannel", 0.95), text="stop", speech_s=0.2)

    assert decision.action is Action.INTERRUPT
    assert decision.reason == "hard_stop_phrase"


def test_a_nod_of_agreement_does_not_interrupt():
    decision = decide_overlap(overlap("backchannel", 0.92), text="mm-hm", speech_s=0.3)

    assert decision.action is Action.CONTINUE


def test_a_long_overlap_interrupts_even_with_no_jev_verdict():
    # Deliberately phrased without a hard-stop opener ("wait", "hang on"), so
    # this exercises the length fallback rather than the regex shortcut.
    decision = decide_overlap(None, text="I don't really follow that part", speech_s=1.8)

    assert decision.action is Action.INTERRUPT
    assert decision.reason == "fallback_long_overlap"


# --- Staged confusion ------------------------------------------------------


def confused_sample(t_ms: int) -> UserState:
    """The demo's staged moment: brows down, lips pressed, gaze away, a pause."""
    return UserState(
        t_ms=t_ms,
        au={"brow_lower": 0.52, "lip_press": 0.31},
        gaze_away=True,
        nod=0,
        wants_turn=False,
        prosody_delta=ProsodyDelta(pitch=0.1, rate=-0.3, pause_ms=900),
        confusion_p=0.71,
    )


def test_a_confusion_sample_survives_the_contract_1_round_trip():
    sample = confused_sample(12_000)

    restored = UserState(**sample.model_dump())

    assert restored.confusion_p == pytest.approx(0.71)
    assert restored.au["brow_lower"] == pytest.approx(0.52)
    assert restored.gaze_away is True
    assert restored.prosody_delta.pause_ms == 900


async def test_user_state_reaches_the_reasoner_through_the_engine():
    engine = StateEngine()
    seen: list[dict] = []

    async def collect(event):
        seen.append(event)

    engine.add_publisher(collect)
    await engine.publish("user_state", **confused_sample(12_000).model_dump())

    assert [e["type"] for e in seen] == ["user_state"]
    assert seen[0]["confusion_p"] == pytest.approx(0.71)


async def test_a_confused_pause_is_held_open_not_answered_over():
    # Mid-explanation the user trails off while confused. The agent should keep
    # listening rather than talk over the hesitation.
    decision = decide_end_of_turn(
        end_of_turn("wait_for_more", 0.8, complete=0.2), text="wait, so the part where", silence_s=0.5
    )

    assert decision.action is Action.HOLD


async def test_the_repair_turn_is_measured_and_published():
    engine = StateEngine()
    published: list[dict] = []

    async def collect(event):
        published.append(event)

    engine.add_publisher(collect)
    hud = LatencyHUD(lambda message: collect(message), log_path=None)

    engine.set_phase(Phase.THINKING)
    sample = await hud.record(total_secs=0.46, stages={"llm inference": 240.0})

    assert engine.state.phase is Phase.THINKING
    assert isinstance(sample, Metrics)
    assert sample.end_of_speech_to_first_audio_ms == 460.0
    assert published[-1]["payload"]["detail"]["median_ms"] == 460.0


def test_high_confusion_triggers_a_probe_question():
    # The staged moment from the demo script: mid-explanation, the driver's
    # brow lowers and lips press for several consecutive ~10Hz samples.
    sample = confused_sample(12_000)
    tracker = ConfusionTracker()

    for _ in range(2):
        streak = tracker.observe(sample.confusion_p)
        decision = decide_probe(
            sample.confusion_p, consecutive_high=streak, bot_speaking=True, already_probing=False
        )
        assert decision.action is Action.CONTINUE, "must not stop on a single noisy reading"

    streak = tracker.observe(sample.confusion_p)
    decision = decide_probe(
        sample.confusion_p, consecutive_high=streak, bot_speaking=True, already_probing=False
    )
    assert decision.action is Action.PROBE
    assert decision.reason == "sustained_confusion"

    # A calm reading resets the streak, so the next brief blip doesn't
    # immediately retrigger a second probe.
    tracker.reset()
    decision = decide_probe(
        sample.confusion_p, consecutive_high=tracker.observe(sample.confusion_p), bot_speaking=True, already_probing=False
    )
    assert decision.action is Action.CONTINUE


async def test_confused_user_state_reaches_a_probe_decision_through_the_engine():
    # End-to-end through the same StateEngine.publish() path lane C's
    # user_state actually travels: the confusion sample is not just a
    # standalone struct, it's what a subscriber sees on the wire.
    engine = StateEngine()
    seen: list[dict] = []

    async def collect(event):
        seen.append(event)

    engine.add_publisher(collect)

    tracker = ConfusionTracker()
    decision = None
    for t_ms in (12_000, 12_100, 12_200):
        await engine.publish("user_state", **confused_sample(t_ms).model_dump())
        streak = tracker.observe(seen[-1]["confusion_p"])
        decision = decide_probe(seen[-1]["confusion_p"], consecutive_high=streak, bot_speaking=True, already_probing=False)

    assert decision is not None and decision.action is Action.PROBE
