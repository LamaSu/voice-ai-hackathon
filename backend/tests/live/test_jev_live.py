"""Live Jev checks: real question sets + policy on realistic interaction states.

Run: uv run pytest -m live tests/live/test_jev_live.py -s
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.jev.client import JevClient
from app.jev.questions import END_OF_TURN_QUESTIONS, OVERLAP_QUESTIONS, to_jev_state
from app.state.interaction_state import InteractionState, Phase
from app.turns.policy import Action, decide_end_of_turn, decide_overlap, is_introduction

pytestmark = pytest.mark.live

NOW = 100.0


def overlap_state(bot_sentence: str, user_text: str, speech_s: float, looking: bool | None = None):
    s = InteractionState(phase=Phase.OVERLAP)
    s.bot.speaking = True
    s.bot.started_at = NOW - 3.0
    s.bot.current_sentence = bot_sentence
    s.bot.spoken_text = bot_sentence
    s.user.vad_speaking = True
    s.user.speech_started_at = NOW - speech_s
    s.user.partial_transcript = user_text
    s.conversation.last_user_turn = "What's the weather like this weekend? I'm thinking Saturday."
    if looking is not None:
        s.vision.enabled = True
        s.vision.face_present = True
        s.vision.looking_at_agent = looking
        s.vision.gaze_confidence = 0.9
    return to_jev_state(s, NOW, overlap_ms=int(speech_s * 1000))


def eot_state(turn_text: str, silence_s: float, last_bot: str = "Hi! How can I help you today?"):
    s = InteractionState(phase=Phase.LISTENING)
    s.user.vad_speaking = False
    s.user.speech_stopped_at = NOW - silence_s
    s.user.turn_text = turn_text
    s.conversation.last_bot_turn = last_bot
    return to_jev_state(s, NOW)


@pytest.fixture
async def jev():
    s = get_settings()
    c = JevClient(s.jev_api_key, s.jev_model, timeout_s=3.0)
    yield c
    await c.aclose()


BOT = "Friday looks rainy, but on Sunday you can expect clear skies and highs around"

OVERLAP_CASES = [
    ("yeah", 0.3, None, {Action.CONTINUE, Action.WAIT}),
    ("mm-hm", 0.3, None, {Action.CONTINUE, Action.WAIT}),
    ("no no, I asked about Saturday", 1.0, None, {Action.INTERRUPT}),
    ("actually can you also check Monday", 1.2, None, {Action.INTERRUPT}),
    ("honey did you feed the dog", 1.2, False, {Action.CONTINUE, Action.WAIT}),
    ("on Sunday you can expect clear skies", 1.0, None, {Action.CONTINUE, Action.WAIT}),
]


@pytest.mark.parametrize("user_text,speech_s,looking,allowed", OVERLAP_CASES)
async def test_overlap_decisions(jev, user_text, speech_s, looking, allowed):
    state = overlap_state(BOT, user_text, speech_s, looking)
    r = await jev.ask(state, OVERLAP_QUESTIONS)
    d = decide_overlap(r, text=user_text, speech_s=speech_s)
    print(f"\n{user_text!r:45} -> {d.action.value:10} ({d.reason}) {r.latency_ms:.0f}ms {r.choices} {r.nouls}")
    assert r.ok, r.error
    assert d.action in allowed


EOT_CASES = [
    ("What's the weather going to be like on Saturday?", 0.4, {Action.RESPOND}),
    ("I want to book a table for", 0.4, {Action.HOLD}),
    ("So I was thinking, um, and", 0.4, {Action.HOLD}),
    ("Hi, my name is Priya.", 0.4, {Action.RESPOND}),
    ("Turn off the lights.", 0.4, {Action.RESPOND}),
]


@pytest.mark.parametrize("text,silence_s,allowed", EOT_CASES)
async def test_end_of_turn_decisions(jev, text, silence_s, allowed):
    r = await jev.ask(eot_state(text, silence_s), END_OF_TURN_QUESTIONS)
    d = decide_end_of_turn(r, text=text, silence_s=silence_s)
    print(f"\n{text!r:50} -> {d.action.value:8} ({d.reason}) {r.latency_ms:.0f}ms {r.choices} {r.nouls}")
    assert r.ok, r.error
    assert d.action in allowed


async def test_introduction_detected(jev):
    r = await jev.ask(eot_state("Hey, I'm Akash by the way.", 0.4), END_OF_TURN_QUESTIONS)
    assert is_introduction(r), r.nouls
    r2 = await jev.ask(eot_state("What's the capital of France?", 0.4), END_OF_TURN_QUESTIONS)
    assert not is_introduction(r2), r2.nouls
