"""Pipeline assembly: one PipelineWorker per WebRTC session.

transport.input -> VAD(signal) -> SpeakerId -> Gradium STT -> InteractionController(Jev)
  -> user aggregator (external turns) -> General Compute LLM -> Gradium TTS
  -> transport.output -> BotTap -> assistant aggregator
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.config import BACKEND_DIR, Settings, get_settings
from app.jev.client import JevClient, JevResult, NullJev
from app.observers.latency_hud import LatencyHUD
from app.fillers import FillerLibrary
from app.memory.store import MemoryLLM, MemoryStore, regex_name
from app.perception.bot_tap import BotTap
from app.perception.speaker_id import SpeakerIdProcessor, new_speaker_memory
from app.perception.vision import apply_faces, apply_gaze, apply_user_state
from app.services import SYSTEM_PROMPT, make_llm, make_stt, make_tts
from app.state.engine import StateEngine
from app.telemetry import SessionTelemetry
from app.state.interaction_state import ConversationState, SpeakerState
from app.turns.controller import InteractionController
from app.turns.policy import is_introduction


class SharedResources:
    """Loaded once per server process and shared by sessions: ECAPA model, memory, Jev client."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.memory = MemoryStore()
        self.memory_llm = MemoryLLM(
            self.settings.general_compute_api_key,
            self.settings.general_compute_base_url,
            self.settings.llm_model,
        )
        self.speakers = new_speaker_memory(self.memory)  # shared by all sessions
        self.fillers = FillerLibrary() if self.settings.enable_fillers else None
        self.sessions: set = set()  # per-session async reset callbacks
        self._embedder = None
        self._embedder_lock = threading.Lock()

    async def reset_all(self) -> dict:
        """Wipe people + voice profiles + summary, and reset every live session's context."""
        self.memory.clear()
        self.speakers = new_speaker_memory()
        for reset in list(self.sessions):
            try:
                await reset()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"session reset failed: {e}")
        logger.info("memory: cleared all people, voice profiles and summary")
        return self.memory.to_ui()

    def embedder(self):
        with self._embedder_lock:
            if self._embedder is None:
                from app.perception.ecapa import EcapaEmbedder

                self._embedder = EcapaEmbedder()
            return self._embedder

    def new_jev(self) -> JevClient | NullJev:
        s = self.settings
        if not s.jev_api_key:
            logger.warning("JEV_API_KEY not set: interaction decisions use deterministic fallbacks only")
            return NullJev()
        return JevClient(s.jev_api_key, s.jev_model, timeout_s=s.jev_timeout_s)


def _jsonl_logger(session_id: str):
    path = BACKEND_DIR / "logs" / f"session-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")

    async def write(event: dict[str, Any]) -> None:
        if event.get("type") == "state" or fh.closed:
            return  # state is too chatty for the log
        fh.write(json.dumps({"wall": time.time(), **event}, default=str) + "\n")
        fh.flush()

    return write, fh, path


def build_session(
    transport: BaseTransport,
    shared: SharedResources,
    *,
    session_id: str | None = None,
    extra_publishers: list | None = None,
) -> tuple[PipelineWorker, StateEngine, InteractionController]:
    s = shared.settings
    session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
    engine = StateEngine()
    memory = shared.memory
    jev = shared.new_jev()

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies(enable_interruptions=False),
            user_turn_stop_timeout=10.0,
        ),
    )

    async def publish_memory() -> None:
        await engine.publish("memory", **memory.to_ui())

    def refresh_system_prompt(speaker_label: str | None) -> None:
        block = memory.prompt_block()
        who = memory.display_name(speaker_label)
        parts = [SYSTEM_PROMPT]
        if block:
            parts.append(block)
        if who:
            parts.append(f"The person speaking right now is {who}.")
        msgs = context.get_messages()
        msgs[0] = {"role": "system", "content": "\n\n".join(parts)}
        context.set_messages(msgs)

    async def before_respond(text: str, speaker_label: str | None) -> str:
        # Who is speaking goes into the system prompt, not the transcript text (keeps the UI clean).
        refresh_system_prompt(speaker_label)
        return text

    async def on_turn_accepted(text: str, speaker_label: str | None, r: JevResult | None) -> None:
        # Jev's introducing_self is the main signal, but it isn't available when a Jev call
        # times out (and the policy answered from its fallback rules), so accept a plain
        # "my name is X" too — otherwise a slow Jev silently costs us the name.
        if not is_introduction(r) and not regex_name(text):
            return
        name = await shared.memory_llm.extract_name(text)
        if not name:
            return
        speaker_label = speaker_label or engine.state.speaker.label
        if not speaker_label:
            # ECAPA classifies the utterance just after the VAD stop; on a first, short
            # introduction the label can land a moment after the turn is accepted.
            for _ in range(8):
                await asyncio.sleep(0.1)
                speaker_label = engine.state.speaker.label
                if speaker_label:
                    break
        if speaker_label:
            memory.set_name(speaker_label, name)
            if engine.state.speaker.label == speaker_label:
                engine.state.speaker.name = name
            logger.info(f"memory: {speaker_label} is {name}")
        else:
            memory.pending_name = name  # bound on the next confident speaker decision
        await engine.publish("interaction", event="introduction", speaker=speaker_label, name=name)
        await publish_memory()

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(start_secs=0.15, stop_secs=0.3, confidence=0.7, min_volume=0.5))
    )
    speaker = SpeakerIdProcessor(
        engine,
        memory,
        enabled=s.enable_speaker_id,
        embedder_factory=shared.embedder,
        on_memory_changed=publish_memory,
        speakers=shared.speakers,
    )
    stt = make_stt(s)
    controller = InteractionController(
        engine,
        jev,
        before_respond=before_respond,
        on_turn_accepted=on_turn_accepted,
        fillers=shared.fillers,
    )
    llm = make_llm(s)
    tts = make_tts(s)
    bot_tap = BotTap(engine, on_response_done=controller.on_response_done)

    pipeline = Pipeline(
        [
            transport.input(),
            vad,
            speaker,
            stt,
            controller,
            user_agg,
            llm,
            tts,
            transport.output(),
            bot_tap,
            assistant_agg,
        ]
    )
    latency_observer = UserBotLatencyObserver()
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, enable_metrics=True),
        idle_timeout_secs=None,
        observers=[latency_observer],
        # The UI should only see user text the controller accepted as a turn, not raw Gradium
        # interims/finals (backchannels, echo) — so the STT is an ignored RTVI source.
        rtvi_observer_params=RTVIObserverParams(ignored_sources=[stt]),
    )

    async def send_to_client(event: dict[str, Any]) -> None:
        await worker.rtvi.send_server_message(event)

    # Contract 4 `metrics` via lane D's LatencyHUD (end of speech -> first audio, per-stage breakdown)
    hud = LatencyHUD(send_to_client)
    hud.attach(latency_observer)

    engine.add_publisher(send_to_client)
    telemetry = SessionTelemetry(session_id, BACKEND_DIR / "logs", settings=s)
    engine.add_publisher(telemetry.publisher())
    # Contract 4 samples land in the session log too, so a slow turn can be read
    # next to the Jev decision and transcript that produced it.
    hud.add_sink(telemetry.metrics_publisher())
    log_writer, log_fh, log_path = _jsonl_logger(session_id)
    engine.add_publisher(log_writer)
    for p in extra_publishers or []:
        engine.add_publisher(p)
    logger.info(f"session {session_id}: event log -> {log_path}")

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        await publish_memory()
        await engine.publish_snapshot(force=True)

    @worker.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, msg):
        if msg.type == "user_state" and isinstance(msg.data, dict):
            apply_user_state(engine, msg.data)  # Contract 1 (lane C)
        elif msg.type == "client_log" and isinstance(msg.data, dict):
            # Browser-side trouble (autoplay blocked, MediaPipe down, WebRTC) is
            # invisible server-side and dies with the console. Land it on disk.
            telemetry.client_log(msg.data)
        elif msg.type == "faces" and isinstance(msg.data, dict):
            apply_faces(engine, msg.data)  # per-face gaze for everyone in frame
        elif msg.type == "gaze" and isinstance(msg.data, dict):
            apply_gaze(engine, msg.data)
        elif msg.type == "reset_memory":
            await shared.reset_all()

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        text = (message.content or "").strip()
        if not text:
            return
        await engine.publish("transcript", role="bot", text=text, interrupted=message.interrupted)
        label = engine.state.speaker.label
        user_text = engine.state.conversation.last_user_turn

        async def update_memory():
            if await shared.memory_llm.update(memory, label, user_text, text):
                await publish_memory()

        asyncio.create_task(update_memory())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"session {session_id}: client disconnected")
        await worker.cancel()

    async def reset_session() -> None:
        speaker.speakers = shared.speakers
        speaker.reset()
        context.set_messages([{"role": "system", "content": SYSTEM_PROMPT}])
        engine.state.speaker = SpeakerState()
        engine.state.conversation = ConversationState()
        await publish_memory()
        await engine.publish("interaction", event="memory_cleared")
        await engine.publish_snapshot(force=True)

    shared.sessions.add(reset_session)

    @worker.event_handler("on_pipeline_finished")
    async def on_finished(worker, frame):
        shared.sessions.discard(reset_session)
        log_fh.close()
        await jev.aclose()

    return worker, engine, controller


async def run_session(transport: BaseTransport, shared: SharedResources, session_id: str | None = None) -> None:
    worker, _, _ = build_session(transport, shared, session_id=session_id)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
