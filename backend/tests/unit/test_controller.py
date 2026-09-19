"""InteractionController behaviour with a scripted fake Jev (no network)."""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.jev.client import JevResult
from app.jev.questions import OVERLAP_QUESTIONS
from app.state.engine import StateEngine
from app.turns.controller import InteractionController
from app.turns.policy import PolicyConfig

KEY = (ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame, InterruptionFrame, TranscriptionFrame)


class FakeJev:
    """Answers based on keywords in user.partial_transcript."""

    def __init__(self, delay: float = 0.02):
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def ask(self, state, questions):
        await asyncio.sleep(self.delay)
        text = state["user"]["partial_transcript"].lower()
        if questions is OVERLAP_QUESTIONS:
            self.calls.append(("overlap", text))
            if any(w in text for w in ("yeah", "mm", "right")):
                return JevResult(
                    nouls={"wants_floor": 0.1, "correcting_agent": 0.0, "addressed_to_agent": 0.8},
                    choices={"intent": {"choice": "backchannel", "confidence": 0.95, "probabilities": {}}},
                )
            return JevResult(
                nouls={"wants_floor": 0.9, "correcting_agent": 0.8, "addressed_to_agent": 0.9},
                choices={"intent": {"choice": "interrupt", "confidence": 0.95, "probabilities": {}}},
            )
        self.calls.append(("eot", text))
        if "honey" in text:
            nxt, complete, addressed = "ignore", 0.9, 0.05
        elif text.endswith(("for", "and", "um")):
            nxt, complete, addressed = "wait_for_more", 0.1, 0.9
        else:
            nxt, complete, addressed = "respond_now", 0.95, 0.9
        return JevResult(
            nouls={"turn_complete": complete, "addressed_to_agent": addressed, "introducing_self": 0.0},
            choices={"next": {"choice": nxt, "confidence": 0.9, "probabilities": {nxt: 0.9}}},
        )


def tr(text):
    return TranscriptionFrame(text, "user", "2026-09-19T00:00:00Z")


def interim(text):
    return InterimTranscriptionFrame(text, "user", "2026-09-19T00:00:00Z")


def key_frames(frames):
    return [f for f in frames if isinstance(f, KEY)]


def names(frames):
    return [type(f).__name__ for f in key_frames(frames)]


def make(jev=None, cfg=PolicyConfig()):
    engine = StateEngine()
    events = []

    async def pub(e):
        events.append(e)

    engine.add_publisher(pub)
    ctrl = InteractionController(engine, jev or FakeJev(), cfg=cfg)
    return ctrl, engine, events


async def test_simple_turn_responds():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            interim("what's the"),
            SleepFrame(0.05),
            VADUserStoppedSpeakingFrame(),
            tr("What's the weather tomorrow?"),
            SleepFrame(0.2),
        ],
    )
    assert names(down) == [
        "ProposedUserStartedSpeakingFrame",
        "TranscriptionFrame",
        "ProposedUserStoppedSpeakingFrame",
    ]
    assert [e["event"] for e in events if e["type"] == "interaction"] == ["user_turn_start", "user_turn_end"]
    assert engine.state.phase.value == "thinking"


async def test_backchannel_does_not_interrupt_or_reach_llm():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
            interim("yeah"),
            SleepFrame(0.2),
            VADUserStoppedSpeakingFrame(),
            tr("Yeah."),
            SleepFrame(0.2),
        ],
    )
    assert names(down) == []
    turn_events = [e["event"] for e in events if e["type"] == "interaction"]
    assert "backchannel" in turn_events and "interrupt" not in turn_events
    assert engine.state.phase.value == "bot_speaking"


async def test_barge_in_interrupts_then_responds_with_full_utterance():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
            interim("no I said"),
            SleepFrame(0.2),
            interim("no I said Saturday"),
            SleepFrame(0.1),
            VADUserStoppedSpeakingFrame(),
            tr("No, I said Saturday."),
            SleepFrame(0.2),
        ],
    )
    assert names(down) == [
        "InterruptionFrame",
        "ProposedUserStartedSpeakingFrame",
        "TranscriptionFrame",
        "ProposedUserStoppedSpeakingFrame",
    ]
    released = [f for f in down if isinstance(f, TranscriptionFrame)][0]
    assert released.text == "No, I said Saturday."  # first words not lost
    interrupt = [e for e in events if e["type"] == "interaction" and e["event"] == "interrupt"][0]
    assert interrupt["interrupted"] is True


async def test_hard_stop_interrupts_without_waiting_for_jev():
    slow = FakeJev(delay=2.0)
    ctrl, engine, events = make(jev=slow)
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
            interim("stop"),
            SleepFrame(0.25),
        ],
        send_end_frame=True,
    )
    assert names(down)[:2] == ["InterruptionFrame", "ProposedUserStartedSpeakingFrame"]
    ev = [e for e in events if e["type"] == "interaction" and e["event"] == "interrupt"][0]
    assert ev["reason"] == "hard_stop_phrase"


async def test_incomplete_turn_holds_then_times_out_into_response():
    ctrl, engine, events = make(cfg=PolicyConfig(hold_max_silence_s=0.4))
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("I want to book a table for"),
            SleepFrame(0.6),
        ],
    )
    turn_events = [e["event"] for e in events if e["type"] == "interaction"]
    assert turn_events.index("hold") < turn_events.index("user_turn_end")
    jev_actions = [e["decision"]["action"] for e in events if e["type"] == "jev"]
    assert jev_actions == ["hold", "respond"]
    assert names(down)[-1] == "ProposedUserStoppedSpeakingFrame"


async def test_user_resuming_cancels_hold():
    ctrl, engine, events = make(cfg=PolicyConfig(hold_max_silence_s=0.5))
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("I want to book a table for"),
            SleepFrame(0.1),
            VADUserStartedSpeakingFrame(),
            SleepFrame(0.6),  # hold would have expired here
            VADUserStoppedSpeakingFrame(),
            tr("two people at seven."),
            SleepFrame(0.2),
        ],
    )
    released = [f for f in down if isinstance(f, TranscriptionFrame)]
    assert len(released) == 1
    assert released[0].text == "I want to book a table for two people at seven."
    assert names(down).count("ProposedUserStoppedSpeakingFrame") == 1


async def test_side_talk_is_dropped():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("honey where are my keys"),
            SleepFrame(0.2),
        ],
    )
    assert names(down) == []
    assert any(e["type"] == "interaction" and e["event"] == "drop" for e in events)


async def test_bot_finished_response_after_backchannel_does_not_create_turn():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
            interim("mm"),
            SleepFrame(0.2),
            BotStoppedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("Mm-hm."),
            SleepFrame(0.3),
        ],
    )
    assert "TranscriptionFrame" not in names(down)


async def test_early_end_of_turn_on_interim_skips_waiting_for_final():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            interim("what's the weather"),
            VADUserStoppedSpeakingFrame(),
            interim("what's the weather tomorrow?"),  # ASR catching up while the user is silent
            SleepFrame(0.2),
            tr("What's the weather tomorrow?"),  # late final: must not create a second turn
            SleepFrame(0.2),
        ],
    )
    assert names(down) == [
        "ProposedUserStartedSpeakingFrame",
        "TranscriptionFrame",
        "ProposedUserStoppedSpeakingFrame",
    ]
    jev = [e for e in events if e["type"] == "jev"]
    assert jev[-1]["decision"]["reason"].endswith("@interim")


async def test_backchannel_prefix_is_stripped_from_next_utterance():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            interim("Yeah."),  # VAD missed the short backchannel
            SleepFrame(0.2),
            VADUserStartedSpeakingFrame(),
            interim("Yeah. Actually, can you"),
            SleepFrame(0.2),
            VADUserStoppedSpeakingFrame(),
            tr("Yeah. Actually, can you make it about a cat?"),
            SleepFrame(0.3),
        ],
    )
    released = [f for f in down if isinstance(f, TranscriptionFrame)]
    assert released and released[0].text == "Actually, can you make it about a cat?"
    assert names(down)[:2] == ["InterruptionFrame", "ProposedUserStartedSpeakingFrame"]


async def test_interim_answered_text_does_not_leak_into_next_turn():
    ctrl, engine, events = make()
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            interim("Hi, my name is Priya."),
            SleepFrame(0.2),  # answered early from interim; Gradium never finalizes it...
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("Priya. What's the weather tomorrow?"),  # ...until the next flush
            SleepFrame(0.2),
        ],
    )
    released = [f.text for f in down if isinstance(f, TranscriptionFrame)]
    assert released == ["Hi, my name is Priya.", "What's the weather tomorrow?"]


async def test_contract2_turn_emitted_with_interrupted_flag():
    ctrl, engine, events = make()
    await run_test(
        ctrl,
        frames_to_send=[
            BotStartedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
            interim("no I said Saturday"),
            SleepFrame(0.2),
            VADUserStoppedSpeakingFrame(),
            tr("No, I said Saturday."),
            SleepFrame(0.2),
        ],
    )
    turns = [e["payload"] for e in events if e["type"] == "turn"]
    assert turns[0]["kind"] == "partial"
    final = [t for t in turns if t["kind"] == "final"]
    assert final and final[-1]["interrupted"] is True and final[-1]["text"] == "No, I said Saturday."
    assert final[-1]["t_speech_end_ms"] is not None


async def test_null_jev_uses_deterministic_fallbacks():
    from app.jev.client import NullJev

    ctrl, engine, events = make(jev=NullJev())
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("What's the weather tomorrow?"),
            SleepFrame(0.2),
        ],
    )
    assert names(down)[-1] == "ProposedUserStoppedSpeakingFrame"
    jev = [e for e in events if e["type"] == "jev"]
    assert jev[-1]["decision"]["reason"].startswith("fallback")


async def test_reset_all_clears_people_profiles_and_sessions(tmp_path):
    import numpy as np

    from app.bot import SharedResources
    from app.memory.store import MemoryStore

    shared = SharedResources.__new__(SharedResources)
    # No settings here on purpose: reset_all() never reads them, and building
    # real Settings calls _require(), which needs a populated .env. That made
    # this test pass only on a machine holding the live keys.
    shared.memory = MemoryStore(tmp_path / "m.json")
    from app.perception.speaker_id import new_speaker_memory

    shared.memory.set_name("S1", "Priya")
    shared.memory.summary = "Priya likes cats."
    shared.speakers = new_speaker_memory(shared.memory)
    shared.speakers.add_profile(np.ones(192, dtype=np.float32), 3.0)
    shared.sessions = set()
    called = []

    async def fake_session_reset():
        called.append(True)

    shared.sessions.add(fake_session_reset)
    ui = await shared.reset_all()
    assert ui == {"people": [], "summary": ""}
    assert shared.speakers.profile_count() == 0
    assert called == [True]
    assert MemoryStore(tmp_path / "m.json").people == {}


class FakeFillers:
    """Stands in for the cached clip library."""

    def __init__(self):
        self.picked = []

    def available(self):
        return True

    def pick(self, category):
        self.picked.append(category)
        return type("F", (), {
            "category": category, "text": f"<{category}>", "pcm": b"\x00\x00" * 4800,
            "sample_rate": 48000, "duration_s": 0.1,
        })()


async def test_filler_is_played_before_the_llm_is_triggered():
    """The clip must precede the turn-stop frame, or it queues behind the TTS answer."""
    from pipecat.frames.frames import SpeechOutputAudioRawFrame

    class FillerJev(FakeJev):
        async def ask(self, state, questions):
            r = await super().ask(state, questions)
            if "next" in r.choices:
                r.choices["filler"] = {
                    "choice": "thinking", "confidence": 0.4,
                    "probabilities": {"none": 0.1, "thinking": 0.6, "casual": 0.3},
                }
            return r

    fillers = FakeFillers()
    engine = StateEngine()
    events = []

    async def pub(e):
        events.append(e)

    engine.add_publisher(pub)
    ctrl = InteractionController(engine, FillerJev(), fillers=fillers)
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("Why do cats purr?"),
            SleepFrame(0.3),
        ],
    )
    kinds = [type(f).__name__ for f in down if isinstance(f, (SpeechOutputAudioRawFrame, *KEY))]
    assert "SpeechOutputAudioRawFrame" in kinds
    assert kinds.index("SpeechOutputAudioRawFrame") < kinds.index("ProposedUserStoppedSpeakingFrame")
    assert fillers.picked == ["thinking"]
    assert any(e["type"] == "interaction" and e["event"] == "filler" for e in events)


async def test_no_filler_when_jev_says_none():
    from pipecat.frames.frames import SpeechOutputAudioRawFrame

    fillers = FakeFillers()
    ctrl, engine, events = make()
    ctrl._fillers = fillers  # FakeJev answers without a "filler" question at all
    down, _ = await run_test(
        ctrl,
        frames_to_send=[
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            tr("Turn off the lights."),
            SleepFrame(0.3),
        ],
    )
    assert not [f for f in down if isinstance(f, SpeechOutputAudioRawFrame)]
    assert fillers.picked == []
