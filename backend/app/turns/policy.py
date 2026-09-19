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


@dataclass(frozen=True)
class PolicyConfig:
    interrupt_intent_conf: float = 0.6
    passive_intent_conf: float = 0.5  # backchannel / side_talk / echo / noise
    wants_floor: float = 0.75
    correcting: float = 0.7
    addressed_min: float = 0.3
    overlap_cap_s: float = 1.5
    overlap_cap_words: int = 3
    turn_complete: float = 0.6
    turn_complete_strong: float = 0.85
    respond_prob: float = 0.35  # p(respond_now) needed when Jev's top choice is wait_for_more
    ignore_conf: float = 0.6
    hold_max_silence_s: float = 2.0
    fallback_respond_silence_s: float = 0.8
    introducing_self: float = 0.6


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


def is_introduction(r: JevResult | None, cfg: PolicyConfig = PolicyConfig()) -> bool:
    return bool(r and r.ok and r.nouls.get("introducing_self", 0.0) >= cfg.introducing_self)
