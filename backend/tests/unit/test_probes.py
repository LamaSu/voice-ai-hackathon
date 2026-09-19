import pytest

from app.contracts import ProsodyDelta, UserState
from app.turns.policy import Action
from app.turns.probes import (
    CONFUSION_HYPOTHESES,
    ProbeCooldown,
    decide_and_pick_probe,
    infer_live_hypotheses,
    pick_probe,
    score_candidate,
)


def sample(**overrides) -> UserState:
    base = dict(
        t_ms=0,
        au={"brow_lower": 0.0, "lip_press": 0.0},
        gaze_away=False,
        nod=0,
        wants_turn=False,
        prosody_delta=ProsodyDelta(pitch=0.0, rate=0.0, pause_ms=0.0),
        confusion_p=0.0,
    )
    base.update(overrides)
    return UserState(**base)


def test_infer_live_hypotheses_maps_signals():
    assert infer_live_hypotheses(sample(au={"brow_lower": 0.5, "lip_press": 0.0})) == {"unclear_terminology"}
    assert infer_live_hypotheses(sample(gaze_away=True)) == {"lost_attention"}
    assert infer_live_hypotheses(sample(prosody_delta=ProsodyDelta(pause_ms=900))) == {"pace_too_fast"}


def test_infer_live_hypotheses_falls_back_to_all_when_no_signal_is_distinctive():
    assert infer_live_hypotheses(sample()) == CONFUSION_HYPOTHESES


def test_infer_live_hypotheses_can_combine():
    result = infer_live_hypotheses(sample(au={"brow_lower": 0.5, "lip_press": 0.0}, gaze_away=True))
    assert result == {"unclear_terminology", "lost_attention"}


def test_score_candidate_is_jaccard_similarity_to_live_hypotheses():
    from app.turns.probes import ProbeCandidate

    candidate = ProbeCandidate("x", hypotheses_covered=frozenset({"pace_too_fast", "lost_attention"}))
    # Exact match: covers exactly what's live.
    assert score_candidate(candidate, frozenset({"pace_too_fast", "lost_attention"})) == 1.0
    # Partial overlap: 1 shared out of 3 in the union.
    assert score_candidate(candidate, frozenset({"pace_too_fast", "unclear_terminology"})) == pytest.approx(1 / 3)
    # Nothing live: no similarity to score.
    assert score_candidate(candidate, frozenset()) == 0.0


def test_score_candidate_penalizes_covering_hypotheses_that_are_not_live():
    from app.turns.probes import ProbeCandidate

    live = frozenset({"lost_attention", "missed_a_step"})
    exact_match = ProbeCandidate("x", hypotheses_covered=live)
    over_broad = ProbeCandidate("y", hypotheses_covered=CONFUSION_HYPOTHESES)
    # A candidate matching the live set exactly beats one that also covers
    # hypotheses nobody currently holds, even though the broad one's raw
    # coverage of `live` is just as complete.
    assert score_candidate(exact_match, live) > score_candidate(over_broad, live)


def test_pick_probe_prefers_full_coverage():
    decision = pick_probe(CONFUSION_HYPOTHESES)
    assert decision.question == "Which part should I go over again?"


def test_pick_probe_prefers_more_specific_when_it_still_covers_everything_live():
    live = frozenset({"lost_attention", "missed_a_step"})
    decision = pick_probe(live)
    assert decision.question == "Still with me, or should I back up?"


class TestProbeCooldown:
    def test_allows_first_probe(self):
        assert ProbeCooldown().allowed(turn_count=1)

    def test_blocks_within_the_window(self):
        cooldown = ProbeCooldown(min_turns_between=3)
        cooldown.record(turn_count=5)
        assert not cooldown.allowed(turn_count=6)
        assert not cooldown.allowed(turn_count=7)
        assert cooldown.allowed(turn_count=8)

    def test_reset(self):
        cooldown = ProbeCooldown()
        cooldown.record(turn_count=5)
        cooldown.reset()
        assert cooldown.allowed(turn_count=6)


def test_decide_and_pick_probe_returns_none_when_not_probing():
    cooldown = ProbeCooldown()
    decision, candidate = decide_and_pick_probe(
        sample(confusion_p=0.1),
        consecutive_high=0,
        bot_speaking=True,
        already_probing=False,
        turn_count=1,
        cooldown=cooldown,
    )
    assert decision.action is Action.CONTINUE
    assert candidate is None


def test_decide_and_pick_probe_fires_and_records_cooldown():
    cooldown = ProbeCooldown(min_turns_between=3)
    confused = sample(
        confusion_p=0.71,
        au={"brow_lower": 0.5, "lip_press": 0.3},
        gaze_away=True,
        prosody_delta=ProsodyDelta(pause_ms=900),
    )

    decision, candidate = decide_and_pick_probe(
        confused,
        consecutive_high=3,
        bot_speaking=True,
        already_probing=False,
        turn_count=1,
        cooldown=cooldown,
    )
    assert decision.action is Action.PROBE
    assert candidate is not None
    assert candidate.question == "Which part should I go over again?"

    # Cooldown blocks a second probe inside the window even though confusion
    # is still high.
    decision2, candidate2 = decide_and_pick_probe(
        confused,
        consecutive_high=5,
        bot_speaking=True,
        already_probing=False,
        turn_count=2,
        cooldown=cooldown,
    )
    assert decision2.action is Action.CONTINUE
    assert decision2.reason == "probe_cooldown"
    assert candidate2 is None
