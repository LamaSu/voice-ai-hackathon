"""Deterministic policy: Jev answers + timing signals -> interaction action.

Jev decides (probabilities), this module decides what those probabilities mean.
All thresholds live in PolicyConfig so they can be tuned and table-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.jev.client import JevResult


class Action(str, Enum):
    INTERRUPT = "interrupt"  # stop bot audio + LLM + TTS, open user turn
    CONTINUE = "continue"  # keep bot talking (backchannel / echo / noise / side talk)
    WAIT = "wait"  # undecided; re-ask when more speech arrives
    RESPOND = "respond"  # close user turn, run LLM
    HOLD = "hold"  # user paused but not done; keep listening
    DROP = "drop"  # speech not for the agent; discard it
    PROBE = "probe"  # stop mid-explanation and ask a non-leading repair question


@dataclass(frozen=True)
class PolicyConfig:
    interrupt_intent_conf: float = 0.6
    passive_intent_conf: float = 0.5  # backchannel / side_talk / echo / noise
    wants_floor: float = 0.75
    correcting: float = 0.7
    addressed_min: float = 0.3
    overlap_cap_s: float = 1.5
    overlap_cap_words: int = 3
    # Cloud ASR delivers the first word ~0.9s late, so waiting for text to decide a barge-in
    # feels sluggish. Speech that keeps going past this is longer than a backchannel
    # ("yeah", "mm-hm", "right" are all shorter), so it interrupts on duration alone —
    # unless Jev has already called this utterance a backchannel.
    overlap_duration_s: float = 0.8
    overlap_min_energy: float = 0.03  # ignore AEC residue of the bot's own voice
    turn_complete: float = 0.6
    turn_complete_strong: float = 0.85
    respond_prob: float = 0.35  # p(respond_now) needed when Jev's top choice is wait_for_more
    ignore_conf: float = 0.6
    hold_max_silence_s: float = 1.1
    fallback_respond_silence_s: float = 0.5
    introducing_self: float = 0.6
    # Jev spreads probability across the filler styles (any of them would be fine), so the
    # decision is "is silence right?" (p(none)), not the top style's confidence.
    filler_none_max: float = 0.6
    filler_min_prob: float = 0.15
    task_conf: float = 0.55  # spinning up an agent is visible, so only on a confident read
    # Gaze gating: with a camera on and faces visible, speech from people who are all looking
    # away is treated as talk in the room, not a query. Stale telemetry disables the gate --
    # a frozen "looking away" must never deafen the agent.
    require_gaze: bool = True
    gaze_stale_s: float = 2.0
    confusion_threshold: float = 0.6  # confusion_p (Contract 1) at/above this counts as "high"
    confusion_confirm_samples: int = 3  # consecutive high samples (~300ms at 10Hz) before acting


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


_HARD_STOP = re.compile(
    r"^\W*(stop|wait|hold on|hang on|pause|shut up|be quiet|quiet|enough|no no|excuse me)\b",
    re.IGNORECASE,
)


def is_hard_stop(text: str) -> bool:
    return bool(_HARD_STOP.search(text.strip()))


def sustained_overlap_interrupt(
    *, speech_s: float, energy: float, resolved_passive: bool, cfg: PolicyConfig = PolicyConfig()
) -> bool:
    """Barge-in on duration, before the transcript arrives."""
    if resolved_passive:
        return False
    return speech_s >= cfg.overlap_duration_s and energy >= cfg.overlap_min_energy


def decide_overlap(
    r: JevResult | None,
    *,
    text: str,
    speech_s: float,
    cfg: PolicyConfig = PolicyConfig(),
) -> Decision:
    words = len(text.split())
    long_enough = speech_s >= cfg.overlap_cap_s and words >= cfg.overlap_cap_words

    if is_hard_stop(text):
        return Decision(Action.INTERRUPT, "hard_stop_phrase")

    if r is None or not r.ok or "intent" not in r.choices:
        if long_enough:
            return Decision(Action.INTERRUPT, "fallback_long_overlap")
        return Decision(Action.WAIT, "jev_unavailable")

    intent = r.choices["intent"]
    choice, conf = intent["choice"], intent["confidence"]
    wants_floor = r.nouls.get("wants_floor", 0.0)
    correcting = r.nouls.get("correcting_agent", 0.0)
    addressed = r.nouls.get("addressed_to_agent", 1.0)

    if choice in ("echo", "noise") and conf >= cfg.passive_intent_conf:
        return Decision(Action.CONTINUE, f"{choice}")
    if choice == "interrupt" and conf >= cfg.interrupt_intent_conf:
        return Decision(Action.INTERRUPT, "intent_interrupt")
    if correcting >= cfg.correcting and addressed >= cfg.addressed_min:
        return Decision(Action.INTERRUPT, "correcting_agent")
    if choice in ("backchannel", "side_talk") and conf >= cfg.passive_intent_conf:
        return Decision(Action.CONTINUE, choice)
    if wants_floor >= cfg.wants_floor and addressed >= cfg.addressed_min:
        return Decision(Action.INTERRUPT, "wants_floor")
    if long_enough and choice != "backchannel":
        return Decision(Action.INTERRUPT, "fallback_long_overlap")
    return Decision(Action.WAIT, f"uncertain:{choice}@{conf:.2f}")


def decide_end_of_turn(
    r: JevResult | None,
    *,
    text: str,
    silence_s: float,
    cfg: PolicyConfig = PolicyConfig(),
) -> Decision:
    if not text.strip():
        return Decision(Action.HOLD, "no_text_yet")

    if silence_s >= cfg.hold_max_silence_s:
        return Decision(Action.RESPOND, "hold_timeout")

    if r is None or not r.ok or "next" not in r.choices:
        if silence_s >= cfg.fallback_respond_silence_s or text.rstrip().endswith(("?", ".", "!")):
            return Decision(Action.RESPOND, "fallback_punctuation_or_silence")
        return Decision(Action.HOLD, "jev_unavailable")

    nxt = r.choices["next"]
    complete = r.nouls.get("turn_complete", 0.0)
    addressed = r.nouls.get("addressed_to_agent", 1.0)

    if nxt["choice"] == "ignore" and nxt["confidence"] >= cfg.ignore_conf and addressed < cfg.addressed_min:
        return Decision(Action.DROP, "not_addressed")
    p_respond = nxt["probabilities"].get("respond_now", 1.0 if nxt["choice"] == "respond_now" else 0.0)
    if complete >= cfg.turn_complete and (nxt["choice"] == "respond_now" or p_respond >= cfg.respond_prob):
        return Decision(Action.RESPOND, "turn_complete")
    if complete >= cfg.turn_complete_strong:
        return Decision(Action.RESPOND, "turn_complete_strong")
    return Decision(Action.HOLD, f"incomplete:{complete:.2f}")


def choose_filler(r: JevResult | None, cfg: PolicyConfig = PolicyConfig()) -> str | None:
    """Which cached filler to play while the LLM generates, or None to stay silent."""
    if not r or not r.ok or "filler" not in r.choices:
        return None
    probs = r.choices["filler"].get("probabilities") or {}
    if not probs:
        return None
    if probs.get("none", 0.0) >= cfg.filler_none_max:
        return None
    category, p = max(((k, v) for k, v in probs.items() if k != "none"), key=lambda kv: kv[1], default=(None, 0.0))
    return category if p >= cfg.filler_min_prob else None


def gaze_blocks_turn(
    *,
    enabled: bool,
    face_count: int,
    looking_count: int,
    age_s: float | None,
    cfg: PolicyConfig = PolicyConfig(),
) -> bool:
    """True when the camera can see people and none of them are looking at the agent.

    Returns False whenever we can't tell: gate off, no camera data, no face in frame, or
    telemetry older than `gaze_stale_s`. Not knowing is not a reason to ignore someone.
    """
    if not cfg.require_gaze or not enabled:
        return False
    if age_s is None or age_s > cfg.gaze_stale_s:
        return False
    if face_count <= 0:
        return False
    return looking_count <= 0


def choose_task(r: JevResult | None, cfg: PolicyConfig = PolicyConfig()) -> str | None:
    """Which background agent this turn should spin up, or None for ordinary conversation."""
    if not r or not r.ok or "task" not in r.choices:
        return None
    c = r.choices["task"]
    if c["choice"] == "none" or c["confidence"] < cfg.task_conf:
        return None
    return c["choice"]


def is_introduction(r: JevResult | None, cfg: PolicyConfig = PolicyConfig()) -> bool:
    return bool(r and r.ok and r.nouls.get("introducing_self", 0.0) >= cfg.introducing_self)


class ConfusionTracker:
    """Counts consecutive user_state samples with confusion_p at or above
    threshold.

    Per COORDINATION.md's decision log, confusion_p is a noisy prior, not a
    verdict, so decide_probe should act on sustained confusion rather than a
    single reading. Reset whenever the bot starts a new utterance so a probe
    decision is always about the explanation currently in progress.
    """

    def __init__(self, cfg: PolicyConfig = PolicyConfig()):
        self._cfg = cfg
        self._consecutive = 0

    def observe(self, confusion_p: float) -> int:
        if confusion_p >= self._cfg.confusion_threshold:
            self._consecutive += 1
        else:
            self._consecutive = 0
        return self._consecutive

    def reset(self) -> None:
        self._consecutive = 0


def decide_probe(
    confusion_p: float,
    *,
    consecutive_high: int,
    bot_speaking: bool,
    already_probing: bool,
    cfg: PolicyConfig = PolicyConfig(),
) -> Decision:
    """Decide whether sustained high confusion_p should interrupt the bot's
    explanation to ask a non-leading repair question (B2, the demo's core
    repair moment).

    This only decides *whether* to stop and probe. Picking the actual
    non-leading question and enforcing the "at most one probe per 3 turns"
    cap is the probe-question picker (#6); this stays a pure trigger so the
    picker can call it without duplicating the threshold logic.
    """
    if already_probing:
        return Decision(Action.CONTINUE, "probe_already_in_flight")
    if not bot_speaking:
        return Decision(Action.CONTINUE, "not_mid_explanation")
    if confusion_p < cfg.confusion_threshold:
        return Decision(Action.CONTINUE, "confusion_below_threshold")
    if consecutive_high < cfg.confusion_confirm_samples:
        return Decision(Action.CONTINUE, "confusion_not_sustained")
    return Decision(Action.PROBE, "sustained_confusion")
