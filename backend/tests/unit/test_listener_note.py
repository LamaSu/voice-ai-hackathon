"""The face must change the *words*, not just the timing."""

from __future__ import annotations

import pytest

from app.listener_note import listener_note
from app.state.interaction_state import VisionState


def vision(**kw) -> VisionState:
    base = dict(enabled=True, face_present=True, looking_at_agent=True, updated_at=100.0)
    base.update(kw)
    return VisionState(**base)


def test_nothing_is_said_when_there_is_no_camera():
    assert listener_note(VisionState()) is None


def test_nothing_is_said_when_the_face_is_lost():
    assert listener_note(vision(face_present=False)) is None


def test_a_calm_attentive_listener_produces_no_note():
    # Prompt tokens cost time-to-first-token; say nothing when there is nothing to say.
    assert listener_note(vision(confusion_p=0.1)) is None


def test_strong_confusion_tells_the_model_to_back_up_and_re_explain():
    note = listener_note(vision(confusion_p=0.85))

    assert note is not None
    assert "back up" in note and "concrete example" in note


def test_mild_confusion_only_asks_for_a_slower_simpler_pace():
    note = listener_note(vision(confusion_p=0.5))

    assert "slow down" in note
    assert "back up" not in note  # not yet — a nudge, not a repair


@pytest.mark.parametrize("confusion_p", [0.5, 0.85])
def test_the_agent_is_never_allowed_to_mention_the_face(confusion_p):
    # Saying "you look confused" is leading, invites a reflexive "no, I'm fine",
    # and is the emotion-detection claim COORDINATION.md explicitly disowns.
    note = listener_note(vision(confusion_p=confusion_p))

    assert "Do not mention their face" in note
    assert "do not ask whether they are" in note


def test_looking_away_asks_for_brevity():
    note = listener_note(vision(looking_at_agent=False, confusion_p=0.1))

    assert "looked away" in note and "brief" in note


def test_nodding_says_keep_going():
    note = listener_note(vision(nod=1, confusion_p=0.1))

    assert "nodding" in note and "keep going" in note


def test_confusion_outranks_nodding_rather_than_contradicting_it():
    # Both can be true in one sample; "keep going at this pace" alongside
    # "back up and re-explain" would be incoherent instruction.
    note = listener_note(vision(nod=1, confusion_p=0.85))

    assert "back up" in note
    assert "keep going at this pace" not in note


def test_wanting_the_turn_asks_the_agent_to_wrap_up():
    note = listener_note(vision(wants_turn=True, confusion_p=0.1))

    assert "about to speak" in note and "hand back" in note


def test_a_stale_reading_is_ignored():
    # A frozen value describes a moment that has passed; acting on it is worse
    # than acting on nothing.
    assert listener_note(vision(confusion_p=0.9, updated_at=100.0), now=104.0) is None


def test_a_fresh_reading_is_used():
    assert listener_note(vision(confusion_p=0.9, updated_at=100.0), now=100.5) is not None


def test_the_note_stays_short_because_prompt_tokens_are_latency():
    note = listener_note(vision(confusion_p=0.9, looking_at_agent=False, wants_turn=True))

    assert len(note) < 600
