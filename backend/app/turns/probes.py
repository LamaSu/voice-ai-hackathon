"""B3: pick which non-leading question to ask once decide_probe (#5) says PROBE.

Per COORDINATION.md's decision log, confusion_p is a prior confirmed by asking,
not a verdict — so this module never asks the user to confirm a specific guess
("you look confused about X?"). It infers which *hypotheses* about the source
of confusion are live from Contract 1 signals, then picks whichever candidate
question's possible answers would separate the most of them. The generic
"which part should I go over again?" always covers every hypothesis (an
open-ended answer can reveal any of them), so it's the safe fallback when nothing
more specific scores higher.

Enforcing "at most one probe per 3 turns" lives here (ProbeCooldown), not in
decide_probe, which stays a pure confusion-threshold trigger.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import UserState
from app.turns.policy import Action, Decision, PolicyConfig, decide_probe

# Coarse, non-exhaustive reasons a listener might be lost. Multiple can be
# live at once; we're not claiming to know which one is right (that would be
# a deception/microexpression-style claim, explicitly out of scope).
CONFUSION_HYPOTHESES = frozenset({"pace_too_fast", "unclear_terminology", "missed_a_step", "lost_attention"})


def infer_live_hypotheses(sample: UserState) -> frozenset[str]:
    """Map Contract 1 signals to the confusion hypotheses they're consistent
    with. Falls back to "could be anything" when no signal is distinctive,
    rather than guessing."""
    hyps: set[str] = set()
    if sample.prosody_delta.pause_ms >= 600 or sample.prosody_delta.rate <= -0.15:
        hyps.add("pace_too_fast")
    if sample.au.get("brow_lower", 0.0) >= 0.3:
        hyps.add("unclear_terminology")
    if sample.au.get("lip_press", 0.0) >= 0.2:
        hyps.add("missed_a_step")
    if sample.gaze_away:
        hyps.add("lost_attention")
    return frozenset(hyps) if hyps else CONFUSION_HYPOTHESES


@dataclass(frozen=True)
class ProbeCandidate:
    question: str
    # Hypotheses whose answer this question's response could help separate.
    hypotheses_covered: frozenset[str]


# The first candidate is deliberately maximal-coverage: an open answer can
# name any part of the explanation, so it never fails to separate whatever
# is actually live. More specific candidates only win when they cover
# everything currently live AND are more direct.
PROBE_CANDIDATES: tuple[ProbeCandidate, ...] = (
    ProbeCandidate("Which part should I go over again?", hypotheses_covered=CONFUSION_HYPOTHESES),
    ProbeCandidate(
        "Want me to slow down, or go over something specific?",
        hypotheses_covered=frozenset({"pace_too_fast", "unclear_terminology", "missed_a_step"}),
    ),
    ProbeCandidate(
        "Still with me, or should I back up?",
        hypotheses_covered=frozenset({"lost_attention", "missed_a_step"}),
    ),
)


def score_candidate(candidate: ProbeCandidate, live_hypotheses: frozenset[str]) -> float:
    """Jaccard similarity between what this question could separate and what's
    actually live. Plain coverage (covered / live) would always favor the
    fully-generic candidate, since its coverage is the whole universe and can
    never lose — it would make every other candidate dead code. Penalizing
    coverage of hypotheses that aren't live rewards a question that matches
    the live set more precisely, so a more specific candidate can win when it
    fits exactly, while the generic one remains the best fallback when the
    live set is genuinely everything (i.e. no signal was distinctive)."""
    union = candidate.hypotheses_covered | live_hypotheses
    if not union:
        return 0.0
    covered = candidate.hypotheses_covered & live_hypotheses
    return len(covered) / len(union)


def pick_probe(
    live_hypotheses: frozenset[str],
    candidates: tuple[ProbeCandidate, ...] = PROBE_CANDIDATES,
) -> ProbeCandidate:
    """Highest-scoring candidate; ties keep the earlier (more generic) one."""
    return max(candidates, key=lambda c: score_candidate(c, live_hypotheses))


class ProbeCooldown:
    """Enforces 'at most one probe per 3 turns' (#6), counted in user turns
    rather than wall-clock time so a fast back-and-forth doesn't get more
    probes than a slow one."""

    def __init__(self, min_turns_between: int = 3):
        self._min_turns_between = min_turns_between
        self._last_probe_turn: int | None = None

    def allowed(self, turn_count: int) -> bool:
        return self._last_probe_turn is None or (turn_count - self._last_probe_turn) >= self._min_turns_between

    def record(self, turn_count: int) -> None:
        self._last_probe_turn = turn_count

    def reset(self) -> None:
        self._last_probe_turn = None


def decide_and_pick_probe(
    sample: UserState,
    *,
    consecutive_high: int,
    bot_speaking: bool,
    already_probing: bool,
    turn_count: int,
    cooldown: ProbeCooldown,
    cfg: PolicyConfig = PolicyConfig(),
) -> tuple[Decision, ProbeCandidate | None]:
    """The single entry point the (not yet built) InteractionController
    should call: combines #5's threshold trigger with this issue's cooldown
    and question choice. Returns the question alongside the decision only
    when it actually fires PROBE."""
    decision = decide_probe(
        sample.confusion_p,
        consecutive_high=consecutive_high,
        bot_speaking=bot_speaking,
        already_probing=already_probing,
        cfg=cfg,
    )
    if decision.action is not Action.PROBE:
        return decision, None
    if not cooldown.allowed(turn_count):
        return Decision(Action.CONTINUE, "probe_cooldown"), None

    candidate = pick_probe(infer_live_hypotheses(sample))
    cooldown.record(turn_count)
    return decision, candidate
