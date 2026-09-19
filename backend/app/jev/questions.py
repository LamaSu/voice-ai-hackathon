"""Jev question sets and the InteractionState -> Jev state serializer.

Each set is sent as ONE Jev request (fan-out): all questions see the same state
snapshot and are answered in parallel. Questions are atomic; combining them into
an action is the job of app.turns.policy.
"""

from __future__ import annotations

from typing import Any

from typesafe_sdk import Choice, Noul

from app.state.interaction_state import InteractionState

OVERLAP_INTENTS = ("interrupt", "backchannel", "side_talk", "echo", "noise")
NEXT_ACTIONS = ("respond_now", "wait_for_more", "ignore")

# A. User speech while the bot is talking.
OVERLAP_QUESTIONS: dict[str, Any] = {
    "intent": Choice(
        instructions=(
            "The voice agent is currently speaking (bot.current_sentence) and the user started "
            "talking over it (user.partial_transcript). Classify what the user's overlapping "
            "speech is."
        ),
        criteria={
            "interrupt": "The user wants the agent to stop: takes the turn, corrects the agent, "
            "disagrees, changes topic, asks a new question, or says stop/wait.",
            "backchannel": "Short listener feedback that expects the agent to keep going, e.g. "
            "'yeah', 'mm-hm', 'right', 'okay', 'uh-huh', 'I see', 'sure'.",
            "side_talk": "The user is talking to someone else in the room, not to the agent.",
            "echo": "The transcript just repeats the agent's own words (speaker audio leaking "
            "into the microphone).",
            "noise": "Not meaningful speech: cough, laugh, breath, filler, or unintelligible.",
        },
    ),
    "wants_floor": Noul(
        instructions="Is the user trying to take the conversational turn away from the agent right now?"
    ),
    "correcting_agent": Noul(
        instructions="Is the user correcting, contradicting, or objecting to what the agent said?"
    ),
    "addressed_to_agent": Noul(
        instructions=(
            "Is the user's speech directed at the agent (consider vision.looking_at_agent and the "
            "content), rather than at another person?"
        )
    ),
}

# B. User went silent while their turn is open.
END_OF_TURN_QUESTIONS: dict[str, Any] = {
    "turn_complete": Noul(
        instructions=(
            "The user just paused (user.silence_ms). Has the user finished their thought and is now "
            "waiting for the agent to reply? Answer no if the sentence is cut off, ends with a "
            "filler/conjunction ('and', 'so', 'um', 'because'), or is clearly mid-list."
        )
    ),
    "addressed_to_agent": Noul(
        instructions="Is this utterance directed at the voice agent rather than at another person?"
    ),
    "next": Choice(
        instructions="What should the voice agent do now?",
        criteria={
            "respond_now": "The user is done and expects a reply.",
            "wait_for_more": "The user is likely to keep talking; stay silent and keep listening.",
            "ignore": "The speech was not for the agent (side talk, noise, self-talk); do not reply.",
        },
    ),
    "introducing_self": Noul(
        instructions="Is the user telling the agent their own name (introducing themselves)?"
    ),
}

# C. Start of a user turn while the bot is silent (used for multi-person gating).
TURN_START_QUESTIONS: dict[str, Any] = {
    "addressed_to_agent": Noul(
        instructions=(
            "Is the person who just started talking addressing the voice agent (consider gaze and "
            "content), rather than another person in the room?"
        )
    ),
}


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def to_jev_state(
    state: InteractionState,
    now: float,
    *,
    transcript: str | None = None,
    overlap_ms: int = 0,
) -> dict[str, Any]:
    """Compact, text-friendly snapshot. Keep it small: tokens are latency."""
    s = state
    speech_ms = (
        int((now - s.user.speech_started_at) * 1000)
        if s.user.vad_speaking and s.user.speech_started_at
        else 0
    )
    silence_ms = (
        int((now - s.user.speech_stopped_at) * 1000)
        if not s.user.vad_speaking and s.user.speech_stopped_at
        else 0
    )
    text = transcript if transcript is not None else (s.user.turn_text + " " + s.user.partial_transcript).strip()
    out: dict[str, Any] = {
        "phase": s.phase.value,
        "bot": {
            "speaking": s.bot.speaking,
            "current_sentence": s.bot.current_sentence[-200:],
            "spoken_so_far": s.bot.spoken_text[-300:],
            "speaking_ms": int((now - s.bot.started_at) * 1000) if s.bot.speaking and s.bot.started_at else 0,
        },
        "user": {
            "speaker": s.speaker.name or s.speaker.label or "unknown",
            "speaker_confidence": _r(s.speaker.confidence),
            "speaking": s.user.vad_speaking,
            "speech_ms": speech_ms,
            "silence_ms": silence_ms,
            "partial_transcript": text,
            "word_count": len(text.split()),
            "energy": _r(s.user.energy),
        },
        "audio": {"overlap_ms": overlap_ms},
        "conversation": {
            "last_user_turn": s.conversation.last_user_turn[-300:],
            "last_bot_turn": s.conversation.last_bot_turn[-300:],
        },
    }
    if s.vision.enabled:
        out["vision"] = {
            "face_present": s.vision.face_present,
            "looking_at_agent": s.vision.looking_at_agent,
            "gaze_confidence": _r(s.vision.gaze_confidence),
            "head_yaw_deg": _r(s.vision.head_yaw, 1),
            "head_pitch_deg": _r(s.vision.head_pitch, 1),
        }
    return out
