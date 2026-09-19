"""The single source of truth every perception module writes into and Jev reads from."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Phase(str, Enum):
    IDLE = "idle"  # nobody talking, nothing pending
    LISTENING = "listening"  # user turn open (user speaking or pausing mid-turn)
    THINKING = "thinking"  # user turn ended, waiting for LLM/TTS audio
    BOT_SPEAKING = "bot_speaking"
    OVERLAP = "overlap"  # user speech while the bot is speaking, not yet resolved


class BotState(BaseModel):
    speaking: bool = False
    started_at: float | None = None
    current_sentence: str = ""
    spoken_text: str = ""  # words of the current response actually played so far
    response_done: bool = True  # LLM response end frame has reached the output transport


class UserState(BaseModel):
    vad_speaking: bool = False
    speech_started_at: float | None = None
    speech_stopped_at: float | None = None
    partial_transcript: str = ""  # live interim text of the current utterance
    turn_text: str = ""  # finalized text accumulated for the current (open) user turn
    energy: float = 0.0  # smoothed RMS 0..1


class SpeakerState(BaseModel):
    label: str | None = None  # S1, S2 ... (ECAPA cluster)
    name: str | None = None  # bound from self-introduction
    confidence: float = 0.0
    unknown_probability: float = 1.0
    probabilities: dict[str, float] = Field(default_factory=dict)
    source: str = "none"  # live | final
    known_speakers: int = 0


class VisionState(BaseModel):
    enabled: bool = False
    face_present: bool = False
    looking_at_agent: bool = False
    gaze_confidence: float = 0.0
    gaze_x: float = 0.5
    gaze_y: float = 0.5
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    head_roll: float = 0.0
    # Contract 1 user_state extras (lane C)
    wants_turn: bool = False
    confusion_p: float = 0.0
    nod: int = 0
    au: dict[str, float] = Field(default_factory=dict)
    # everyone in frame (lane A `faces` message), for multi-person turn decisions
    face_count: int = 0
    faces_looking_at_agent: int = 0
    faces: list[dict] = Field(default_factory=list)
    updated_at: float | None = None


class ConversationState(BaseModel):
    last_user_turn: str = ""
    last_bot_turn: str = ""
    turn_count: int = 0
    summary: str = ""


class InteractionState(BaseModel):
    phase: Phase = Phase.IDLE
    bot: BotState = Field(default_factory=BotState)
    user: UserState = Field(default_factory=UserState)
    speaker: SpeakerState = Field(default_factory=SpeakerState)
    vision: VisionState = Field(default_factory=VisionState)
    conversation: ConversationState = Field(default_factory=ConversationState)
    version: int = 0
