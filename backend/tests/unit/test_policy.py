import pytest

from app.jev.client import JevResult
from app.turns.policy import (
    Action,
    ConfusionTracker,
    decide_end_of_turn,
    decide_overlap,
    decide_probe,
    is_hard_stop,
    is_introduction,
)


def overlap_result(intent, conf, wants_floor=0.2, correcting=0.1, addressed=0.8):
    return JevResult(
        nouls={"wants_floor": wants_floor, "correcting_agent": correcting, "addressed_to_agent": addressed},
        choices={"intent": {"choice": intent, "confidence": conf, "probabilities": {}}},
    )


def eot_result(nxt, conf, complete, addressed=0.9, intro=0.0):
    return JevResult(
        nouls={"turn_complete": complete, "addressed_to_agent": addressed, "introducing_self": intro},
        choices={"next": {"choice": nxt, "confidence": conf, "probabilities": {}}},
    )


@pytest.mark.parametrize(
    "result,text,speech_s,expected",
    [
        (overlap_result("interrupt", 0.9, wants_floor=0.9), "no I said Saturday", 0.6, Action.INTERRUPT),
        (overlap_result("backchannel", 0.95), "yeah", 0.3, Action.CONTINUE),
        (overlap_result("backchannel", 0.9, wants_floor=0.9), "mm-hm", 0.3, Action.CONTINUE),
        (overlap_result("echo", 0.8), "tomorrow will be sunny", 0.8, Action.CONTINUE),
        (overlap_result("noise", 0.7), "uh", 0.2, Action.CONTINUE),
        (overlap_result("side_talk", 0.8, addressed=0.1), "honey can you grab that", 1.0, Action.CONTINUE),
        (overlap_result("backchannel", 0.4, correcting=0.8), "that's not right", 0.7, Action.INTERRUPT),
        (overlap_result("backchannel", 0.4, wants_floor=0.8), "hmm but", 0.4, Action.INTERRUPT),
        (overlap_result("side_talk", 0.87, wants_floor=0.8, addressed=0.1), "honey did you feed the dog", 1.2, Action.CONTINUE),
        (overlap_result("side_talk", 0.4), "so what about", 0.5, Action.WAIT),
        (overlap_result("side_talk", 0.4), "so what about the other one", 1.8, Action.INTERRUPT),
        (overlap_result("backchannel", 0.9), "wait a second", 0.3, Action.INTERRUPT),  # hard stop wins
        (None, "so what about", 0.5, Action.WAIT),
        (None, "so what about the other one", 1.6, Action.INTERRUPT),
        (JevResult(ok=False, error="timeout"), "stop", 0.2, Action.INTERRUPT),
    ],
)
def test_decide_overlap(result, text, speech_s, expected):
    assert decide_overlap(result, text=text, speech_s=speech_s).action == expected


@pytest.mark.parametrize(
    "result,text,silence_s,expected",
    [
        (eot_result("respond_now", 0.9, 0.9), "what's the weather tomorrow?", 0.3, Action.RESPOND),
        (eot_result("wait_for_more", 0.8, 0.3), "I was thinking that maybe", 0.3, Action.HOLD),
        (eot_result("wait_for_more", 0.8, 0.3), "I was thinking that maybe", 2.1, Action.RESPOND),
        (eot_result("wait_for_more", 0.6, 0.9), "book it for Saturday", 0.4, Action.RESPOND),
        (eot_result("ignore", 0.9, 0.9, addressed=0.1), "honey where are the keys", 0.4, Action.DROP),
        (eot_result("ignore", 0.9, 0.9, addressed=0.6), "where are the keys", 0.4, Action.RESPOND),
        (eot_result("respond_now", 0.9, 0.9), "", 0.4, Action.HOLD),
        (None, "what time is it?", 0.2, Action.RESPOND),
        (None, "and then", 0.3, Action.HOLD),
        (None, "and then", 0.9, Action.RESPOND),
    ],
)
def test_decide_end_of_turn(result, text, silence_s, expected):
    assert decide_end_of_turn(result, text=text, silence_s=silence_s).action == expected


def test_hard_stop():
    assert is_hard_stop("Stop.")
    assert is_hard_stop("wait, what?")
    assert is_hard_stop("hold on a sec")
    assert not is_hard_stop("I can't wait for Saturday")
    assert not is_hard_stop("yeah")


def test_introduction():
    assert is_introduction(eot_result("respond_now", 0.9, 0.9, intro=0.9))
    assert not is_introduction(eot_result("respond_now", 0.9, 0.9, intro=0.2))
    assert not is_introduction(None)


# --- Confusion probe (B2, #5) -----------------------------------------------


def test_confusion_tracker_counts_consecutive_high_samples():
    tracker = ConfusionTracker()
    assert tracker.observe(0.7) == 1
    assert tracker.observe(0.65) == 2
    assert tracker.observe(0.2) == 0  # a low reading resets the streak
    assert tracker.observe(0.9) == 1


def test_confusion_tracker_reset():
    tracker = ConfusionTracker()
    tracker.observe(0.9)
    tracker.observe(0.9)
    tracker.reset()
    assert tracker.observe(0.9) == 1


@pytest.mark.parametrize(
    "confusion_p,consecutive_high,bot_speaking,already_probing,expected,reason",
    [
        (0.3, 5, True, False, Action.CONTINUE, "confusion_below_threshold"),
        (0.7, 1, True, False, Action.CONTINUE, "confusion_not_sustained"),
        (0.7, 3, False, False, Action.CONTINUE, "not_mid_explanation"),
        (0.7, 3, True, True, Action.CONTINUE, "probe_already_in_flight"),
        (0.7, 3, True, False, Action.PROBE, "sustained_confusion"),
    ],
)
def test_decide_probe(confusion_p, consecutive_high, bot_speaking, already_probing, expected, reason):
    decision = decide_probe(
        confusion_p,
        consecutive_high=consecutive_high,
        bot_speaking=bot_speaking,
        already_probing=already_probing,
    )
    assert decision.action == expected
    assert decision.reason == reason


def test_regex_name_is_the_fallback_when_jev_is_unavailable():
    """Name binding must survive a Jev timeout (bot.on_turn_accepted uses both signals)."""
    from app.memory.store import regex_name

    assert regex_name("Hi there, my name is Priya.") == "Priya"
    assert regex_name("hello, I'm Marcus") == "Marcus"
    assert regex_name("call me Akash") == "Akash"
    assert regex_name("What's the capital of Japan?") is None
    assert regex_name("I'm going to the shops") is None


def filler_result(probs):
    top = max(probs, key=probs.get)
    return JevResult(choices={"filler": {"choice": top, "confidence": probs[top], "probabilities": probs}})


@pytest.mark.parametrize(
    "probs,expected",
    [
        ({"none": 0.87, "acknowledging": 0.12, "thinking": 0.01}, None),  # a command: stay silent
        # silence is plausible but not likely: speak, because hearing something at ~0.3s
        # beats hearing nothing for ~1.2s (filler_none_max = 0.6)
        ({"none": 0.45, "acknowledging": 0.51, "thinking": 0.04}, "acknowledging"),
        ({"none": 0.65, "acknowledging": 0.3, "thinking": 0.05}, None),  # silence likely
        ({"none": 0.1, "weighing": 0.29, "acknowledging": 0.24, "thinking": 0.22}, "weighing"),
        ({"none": 0.2, "thinking": 0.05, "casual": 0.04}, None),  # no style stands out
    ],
)
def test_choose_filler(probs, expected):
    from app.turns.policy import choose_filler

    assert choose_filler(filler_result(probs)) == expected


def test_choose_filler_without_jev():
    from app.turns.policy import choose_filler

    assert choose_filler(None) is None
    assert choose_filler(JevResult(ok=False, error="timeout")) is None


@pytest.mark.parametrize(
    "speech_s,energy,passive,expected",
    [
        (0.9, 0.2, False, True),  # still talking past any backchannel: interrupt on duration
        (0.5, 0.2, False, False),  # short enough to be "yeah" / "mm-hm"
        (1.2, 0.2, True, False),  # Jev already called it a backchannel
        (1.2, 0.01, False, False),  # too quiet: AEC residue of the bot's own voice
    ],
)
def test_sustained_overlap_interrupt(speech_s, energy, passive, expected):
    from app.turns.policy import sustained_overlap_interrupt

    assert sustained_overlap_interrupt(speech_s=speech_s, energy=energy, resolved_passive=passive) is expected


@pytest.mark.parametrize(
    "enabled,faces,looking,age,expected",
    [
        (True, 2, 0, 0.2, True),    # two people in frame, both looking away: room talk
        (True, 1, 1, 0.2, False),   # looking at the agent
        (True, 0, 0, 0.2, False),   # camera on, nobody in frame: can't tell, so listen
        (True, 2, 0, 5.0, False),   # stale telemetry must not deafen the agent
        (True, 2, 0, None, False),  # never received any gaze data
        (False, 2, 0, 0.2, False),  # gate disabled
    ],
)
def test_gaze_blocks_turn(enabled, faces, looking, age, expected):
    from app.turns.policy import gaze_blocks_turn

    assert gaze_blocks_turn(enabled=enabled, face_count=faces, looking_count=looking, age_s=age) is expected
