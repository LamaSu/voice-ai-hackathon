"""StateEngine: shared mutable InteractionState plus a publish hook for the UI."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.services import BOT_NAME
from app.state.interaction_state import InteractionState, Phase

Publisher = Callable[[dict[str, Any]], Awaitable[None]]


class StateEngine:
    """Every perception/cognition module mutates `state` through this object.

    `publish()` fans events out to listeners (RTVI server messages, JSONL log, tests).
    Snapshots of the full state are throttled so the UI gets ~10 Hz updates.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.state = InteractionState()
        self.clock = clock
        self._publishers: list[Publisher] = []
        self._last_snapshot = 0.0
        self._snapshot_interval = 0.1
        self._pending_snapshot: asyncio.TimerHandle | None = None

    def now(self) -> float:
        return self.clock()

    def add_publisher(self, publisher: Publisher) -> None:
        self._publishers.append(publisher)

    def bump(self) -> int:
        self.state.version += 1
        return self.state.version

    def set_phase(self, phase: Phase) -> None:
        if self.state.phase != phase:
            logger.debug(f"phase {self.state.phase.value} -> {phase.value}")
            self.state.phase = phase
            self.bump()

    async def publish(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "t": round(self.now(), 3), **payload}
        for pub in list(self._publishers):
            try:
                await pub(event)
            except Exception as e:  # noqa: BLE001 - UI failures must never break the pipeline
                logger.warning(f"publisher failed for {event_type}: {e}")

    async def publish_contract(self, msg_type: str, payload: dict[str, Any]) -> None:
        """Cross-lane contracts (COORDINATION.md) use the {type, payload} envelope."""
        for pub in list(self._publishers):
            try:
                await pub({"type": msg_type, "payload": payload})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"publisher failed for contract {msg_type}: {e}")

    async def publish_snapshot(self, force: bool = False) -> None:
        now = self.now()
        if not force and now - self._last_snapshot < self._snapshot_interval:
            return
        self._last_snapshot = now
        await self.publish("state", state=self.ui_snapshot())

    def ui_snapshot(self) -> dict[str, Any]:
        s = self.state
        now = self.now()
        user_speech_ms = (
            int((now - s.user.speech_started_at) * 1000)
            if s.user.vad_speaking and s.user.speech_started_at
            else 0
        )
        return {
            "phase": s.phase.value,
            "bot_speaking": s.bot.speaking,
            "bot_sentence": s.bot.current_sentence,
            "user_vad": s.user.vad_speaking,
            "user_speech_ms": user_speech_ms,
            "user_energy": round(s.user.energy, 3),
            "partial": s.user.partial_transcript,
            "speaker": s.speaker.model_dump(),
            "bot_name": BOT_NAME,
            "vision": s.vision.model_dump(),
            "version": s.version,
        }
