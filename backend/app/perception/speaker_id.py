"""Speaker identification processor: ECAPA embeddings + WhoSpeaksLive SpeakerMemory.

Also acts as the audio tap: keeps a rolling 16 kHz buffer and writes user energy to state.

- live:  every 0.4 s while the user speaks, embed the last 1.5 s -> score_existing()
- final: at VAD stop, embed the whole utterance -> classify() (creates/updates S1, S2, ...)
Embedding runs in a worker thread so it never blocks the pipeline or turn decisions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.memory.store import MemoryStore
from app.perception.whospeaks.speaker_embedding_cluster import SpeakerDecision, SpeakerMemory
from app.state.engine import StateEngine

SR = 16000
PREROLL_S = 0.3
LIVE_PERIOD_S = 0.4
LIVE_WINDOW_S = 1.5
LIVE_MIN_S = 0.7
FINAL_MIN_S = 0.8
BUFFER_S = 30.0
UNKNOWN_STITCH_S = 6.0

# WhoSpeaksLive production defaults (src/window/window_cli_speakers.py), loosened slightly
# for ECAPA-only on short conversational turns.
MEMORY_PARAMS = dict(
    same_speaker_similarity=0.43,
    similarity_temperature=0.061,
    speaker_softmax_temperature=0.0557,
    new_speaker_threshold=0.4309,
    duplicate_profile_similarity=0.4247,
    unknown_short_threshold=0.287,
    min_first_speaker_seconds=1.0,
    min_new_speaker_seconds=1.5,
    late_new_speaker_min_seconds=2.5,
    max_speakers=12,
    min_margin=0.0372,
    margin_temperature=0.0361,
    update_unknown_max=0.4289,
)


def new_speaker_memory(memory: MemoryStore | None = None) -> SpeakerMemory:
    """A SpeakerMemory with our params, restored from persisted voice profiles if any."""
    speakers = SpeakerMemory(**MEMORY_PARAMS)
    if memory is not None and memory.profiles:
        try:
            speakers.replace_profiles(memory.profiles)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"could not restore voice profiles: {e}")
    return speakers


def display_probabilities(decision: SpeakerDecision) -> dict[str, float]:
    """SpeakerMemory keys probabilities as 'speakerN'; map them to labels 'SN'."""
    out = {}
    for k, v in decision.probabilities.items():
        out[("S" + k[len("speaker"):]) if k.startswith("speaker") else k] = round(float(v), 3)
    return out


class SpeakerIdProcessor(FrameProcessor):
    def __init__(
        self,
        engine: StateEngine,
        memory: MemoryStore,
        *,
        enabled: bool = True,
        embedder_factory: Callable[[], object] | None = None,
        on_memory_changed: Callable[[], Awaitable[None]] | None = None,
        speakers: SpeakerMemory | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._engine = engine
        self._memory = memory
        self._enabled = enabled
        self._embedder_factory = embedder_factory
        self._embedder = None
        self._on_memory_changed = on_memory_changed
        # One SpeakerMemory shared by every session (pass `speakers`), so concurrent sessions
        # never overwrite each other's voice profiles or reuse labels.
        self.speakers = speakers if speakers is not None else new_speaker_memory(memory)
        self._buf = np.zeros(int(SR * BUFFER_S), dtype=np.float32)
        self._written = 0  # total samples ever written
        self._utt_start: int | None = None
        self._live_task: asyncio.Task | None = None
        self._load_task: asyncio.Task | None = None
        self._energy = 0.0
        self._unknown_tail: np.ndarray | None = None
        self._unknown_at = 0.0

    # ---------- buffer ----------
    def _append(self, pcm16: bytes, sample_rate: int) -> None:
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != SR and len(x):
            n = int(len(x) * SR / sample_rate)
            x = np.interp(np.linspace(0, len(x), n, endpoint=False), np.arange(len(x)), x).astype(np.float32)
        if len(x) == 0:
            return
        rms = float(np.sqrt(np.mean(x * x) + 1e-12))
        self._energy = 0.7 * self._energy + 0.3 * min(1.0, rms * 8)
        self._engine.state.user.energy = self._energy
        n = len(x)
        size = len(self._buf)
        start = self._written % size
        end = start + n
        if end <= size:
            self._buf[start:end] = x
        else:
            k = size - start
            self._buf[start:] = x[:k]
            self._buf[: n - k] = x[k:]
        self._written += n

    def _slice(self, start_abs: int, end_abs: int) -> np.ndarray:
        size = len(self._buf)
        start_abs = max(start_abs, self._written - size + 1)
        if end_abs <= start_abs:
            return np.zeros(0, dtype=np.float32)
        idx = np.arange(start_abs, end_abs) % size
        return self._buf[idx].copy()

    # ---------- embedding ----------
    async def _ensure_embedder(self) -> None:
        if self._embedder is not None or not self._enabled:
            return
        factory = self._embedder_factory
        if factory is None:
            from app.perception.ecapa import EcapaEmbedder

            factory = EcapaEmbedder
        logger.info("speaker-id: loading ECAPA model...")
        self._embedder = await asyncio.to_thread(factory)
        logger.info("speaker-id: ECAPA ready")

    async def _embed(self, audio: np.ndarray) -> np.ndarray | None:
        if self._embedder is None:
            return None
        return await asyncio.to_thread(self._embedder.embed, audio)  # type: ignore[attr-defined]

    def reset(self) -> None:
        """Forget in-flight speaker state (after a memory wipe)."""
        self._unknown_tail = None
        self._utt_start = None

    def _apply(self, decision: SpeakerDecision, source: str) -> None:
        sp = self._engine.state.speaker
        label = decision.assigned_speaker
        sp.label = label
        sp.name = self._memory.display_name(label) if label else None
        if sp.name == label:
            sp.name = None
        sp.unknown_probability = round(float(decision.unknown_probability), 3)
        sp.probabilities = display_probabilities(decision)
        sp.confidence = round(sp.probabilities.get(label, 0.0), 3) if label else 0.0
        sp.source = source
        sp.known_speakers = self.speakers.profile_count()

    async def _live_loop(self, utt_start: int) -> None:
        try:
            while True:
                await asyncio.sleep(LIVE_PERIOD_S)
                dur = (self._written - utt_start) / SR
                if dur < LIVE_MIN_S or self.speakers.profile_count() == 0:
                    continue
                window = self._slice(self._written - int(LIVE_WINDOW_S * SR), self._written)
                emb = await self._embed(window)
                if emb is None:
                    continue
                decision = self.speakers.score_existing(emb, min(dur, LIVE_WINDOW_S))
                self._apply(decision, "live")
                await self._engine.publish_snapshot()
        except asyncio.CancelledError:
            pass

    async def _finalize(self, audio: np.ndarray) -> None:
        dur = len(audio) / SR
        emb = await self._embed(audio)
        if emb is None:
            return
        learn = not self._engine.state.bot.speaking
        decision = self.speakers.classify(emb, dur, allow_new_speaker=learn, allow_profile_update=learn)
        now = self._engine.now()
        if (
            decision.assigned_speaker is None
            and learn
            and self._unknown_tail is not None
            and now - self._unknown_at < UNKNOWN_STITCH_S
        ):
            # Short unknown utterances in a row are likely one new person: stitch and retry.
            combined = np.concatenate([self._unknown_tail, audio])[-int(8 * SR):]
            emb2 = await self._embed(combined)
            if emb2 is not None:
                audio, dur = combined, len(combined) / SR
                decision = self.speakers.classify(emb2, dur, allow_new_speaker=True, allow_profile_update=True)
        if decision.assigned_speaker is None and decision.unknown_probability >= 0.6 and learn:
            self._unknown_tail, self._unknown_at = audio, now
        else:
            self._unknown_tail = None
        self._apply(decision, "final")
        label = decision.assigned_speaker
        if label:
            self._memory.note_utterance(label, dur)
            pending = self._memory.pending_name
            if pending and not self._memory.person(label).name:
                self._memory.pending_name = None
                self._memory.set_name(label, pending)
                self._apply(decision, "final")
                logger.info(f"memory: bound pending name {pending} -> {label}")
        if learn:
            self._memory.profiles = self.speakers.export_profiles()
            self._memory.save()
        logger.debug(
            f"speaker-id final: {label} created={decision.created_speaker} "
            f"top={decision.top_similarity} unknown={decision.unknown_probability:.2f} ({dur:.1f}s)"
        )
        await self._engine.publish_snapshot(force=True)
        if self._on_memory_changed and (decision.created_speaker or label):
            await self._on_memory_changed()

    # ---------- frames ----------
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame) and self._enabled:
            self._load_task = self.create_task(self._ensure_embedder(), "ecapa_load")
        elif isinstance(frame, InputAudioRawFrame):
            self._append(frame.audio, frame.sample_rate)
        elif isinstance(frame, VADUserStartedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            self._utt_start = max(0, self._written - int(PREROLL_S * SR))
            if self._enabled:
                if self._live_task:
                    await self.cancel_task(self._live_task)
                self._live_task = self.create_task(self._live_loop(self._utt_start), "speaker_live")
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._live_task:
                await self.cancel_task(self._live_task)
                self._live_task = None
            if self._enabled and self._utt_start is not None:
                audio = self._slice(self._utt_start, self._written)
                self._utt_start = None
                if len(audio) / SR >= FINAL_MIN_S:
                    self.create_task(self._finalize(audio), "speaker_final")
        elif isinstance(frame, (EndFrame, CancelFrame)):
            if self._live_task:
                await self.cancel_task(self._live_task)
                self._live_task = None

        await self.push_frame(frame, direction)
