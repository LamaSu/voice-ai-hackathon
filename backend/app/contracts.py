"""Contract payloads from COORDINATION.md, as typed structures.

These mirror the four cross-lane contracts exactly. They are read-only:
changing a shape here is a contract change and needs a `contract-change`
issue plus a heads-up to every lane owner. Lanes import from here so a
typo in one lane cannot silently diverge from another.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Contract 1: user_state (C -> B), ~10 Hz, values are deltas from the
# user's own baseline captured in the first 30 seconds.


class ProsodyDelta(BaseModel):
    """Vocal prosody, as a delta from the user's baseline."""

    pitch: float = 0.0
    rate: float = 0.0
    pause_ms: float = 0.0


class UserState(BaseModel):
    """A single ~10 Hz perception sample from the browser.

    Not to be confused with ``app.state.interaction_state.UserState``, which is
    the engine's internal view of the speaking user. This one is the wire
    format lane C publishes and lane B consumes.
    """

    t_ms: int
    au: dict[str, float] = Field(default_factory=dict)
    gaze_away: bool = False
    nod: int = 0
    wants_turn: bool = False
    prosody_delta: ProsodyDelta = Field(default_factory=ProsodyDelta)
    confusion_p: float = 0.0


# Contract 2: turn (A -> B)


class Turn(BaseModel):
    """A transcript turn, partial or final."""

    kind: Literal["partial", "final"]
    text: str
    t_speech_end_ms: int | None = None
    interrupted: bool = False


# Contract 3: speak (B -> A)


class Speak(BaseModel):
    """A streamed chunk of agent speech, or a cancel."""

    text: str = ""
    style: str | None = None
    cancel: bool = False


# Contract 4: metrics (all -> D)


class Metrics(BaseModel):
    """Stage timestamps so the latency HUD can show end of speech to first audio."""

    t_ms: int
    end_of_speech_to_first_audio_ms: float | None = None
    stages: dict[str, float] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)


# RTVI message types carrying these contracts over the transport.
MSG_USER_STATE = "user_state"
MSG_TURN = "turn"
MSG_SPEAK = "speak"
MSG_METRICS = "metrics"
