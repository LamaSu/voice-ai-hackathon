"""InteractionController: the only component that opens/closes user turns or interrupts the bot.

Sits between the STT and the user context aggregator. The aggregator is configured with
ExternalUserTurnStrategies(enable_interruptions=False), so turns only happen when this
processor pushes ProposedUserStarted/StoppedSpeakingFrame, and the bot is only interrupted
when this processor pushes an InterruptionFrame downstream (flushes LLM, TTS, output audio).

Final transcripts are HELD here and released to the aggregator only once the speech has
been accepted as a user turn (RESPOND / INTERRUPT). Backchannels, echo and side talk are
dropped and never reach the LLM context.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import re

from app.contracts import MSG_TURN, Turn
from app.jev.client import JevLike, JevResult
from app.jev.questions import END_OF_TURN_QUESTIONS, OVERLAP_QUESTIONS, to_jev_state
from app.state.engine import StateEngine
from app.state.interaction_state import Phase
from app.turns.policy import (
    Action,
    Decision,
    PolicyConfig,
    decide_end_of_turn,
    decide_overlap,
    is_hard_stop,
    is_introduction,
)

OVERLAP_MIN_INTERVAL_S = 0.15
OVERLAP_FINAL_WAIT_S = 1.2  # after VAD stop in overlap, how long to wait for the final transcript
EOT_FINAL_WAIT_S = 1.0  # after VAD stop in a turn, how long to wait for the final transcript
THINKING_TIMEOUT_S = 12.0

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", "", text.lower())).strip()


BeforeRespond = Callable[[str, str | None], Awaitable[str]]
OnTurnAccepted = Callable[[str, str | None, JevResult | None], Awaitable[None]]


def _done(result):
    """Wrap an already-computed result as an awaitable so it can stand in for a spec task."""
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(result)
    return fut


def _strip_prefix(text: str, prefix_norm: str) -> str:
    """Remove a normalized prefix (e.g. a discarded 'yeah') from the start of raw text."""
    words = text.split()
    n = len(prefix_norm.split())
    return " ".join(words[n:]).strip() if _norm(" ".join(words[:n])) == prefix_norm else text


def _strip_leaked_tail(text: str, consumed_norm: str) -> str:
    """If `text` starts with a sentence-final tail of already-consumed text, drop that tail.

    Gradium can finalize the last word(s) of an utterance we already answered from interim text
    together with the NEXT utterance ("Priya. What's the weather?"). Only tails that end with
    sentence punctuation in the raw text are stripped, to avoid eating genuine repeated words.
    """
    words = text.split()
    consumed = consumed_norm.split()
    for n in range(min(len(words), len(consumed), 6), 0, -1):
        head = words[:n]
        if head[-1][-1:] in ".?!," and _norm(" ".join(head)).split() == consumed[-n:]:
            return " ".join(words[n:]).strip()
    return text


class InteractionController(FrameProcessor):
    def __init__(
        self,
        engine: StateEngine,
        jev: JevLike,
        *,
        cfg: PolicyConfig = PolicyConfig(),
        before_respond: BeforeRespond | None = None,
        on_turn_accepted: OnTurnAccepted | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._engine = engine
        self._jev = jev
        self._cfg = cfg
        self._before_respond = before_respond
        self._on_turn_accepted = on_turn_accepted

        self._held: list[TranscriptionFrame] = []
        self._turn_open = False  # ProposedUserStartedSpeakingFrame sent, stop not yet sent
        self._utt = 0  # increments on every VAD start; stale-decision guard
        self._discard_utt = -1  # utterance classified as backchannel/noise; late finals are dropped
        self._discarded_text = ""  # text of the last discarded backchannel (stripped if it leaks)
        # speculative end-of-turn: Jev asked at VAD stop on the interim text
        self._spec: tuple[int, str, asyncio.Task] | None = None
        self._early_task: asyncio.Task | None = None
        self._answered_utt = -1  # utterance already answered from interim text; its late final is dropped

        # overlap (user speech while bot speaks / thinks)
        self._overlap = False
        self._overlap_utt = 0
        self._overlap_resolved: Action | None = None
        self._overlap_started = 0.0
        self._overlap_task: asyncio.Task | None = None
        self._overlap_dirty = False
        self._overlap_last_ask = 0.0
        self._overlap_asked_text = None
        self._overlap_watchdog: asyncio.Task | None = None
        self._overlap_end_task: asyncio.Task | None = None

        # end of turn
        self._eot_task: asyncio.Task | None = None
        self._hold_task: asyncio.Task | None = None
        self._final_wait_task: asyncio.Task | None = None
        self._thinking_task: asyncio.Task | None = None
        # True from RESPOND until the bot's response has fully played (or was interrupted).
        # Covers LLM latency and the gaps between TTS sentences.
        self._awaiting_bot = False
        self._interrupted_this_turn = False  # Contract 2: final turn carries interrupted=true

    # ------------------------------------------------------------------ helpers
    @property
    def s(self):
        return self._engine.state

    def _now(self) -> float:
        return self._engine.now()

    def _held_text(self) -> str:
        return " ".join(f.text.strip() for f in self._held if f.text.strip()).strip()

    def _cancel(self, name: str) -> None:
        t: asyncio.Task | None = getattr(self, name)
        if t and not t.done():
            t.cancel()
        setattr(self, name, None)

    async def _publish_jev(self, set_name: str, r: JevResult | None, d: Decision, text: str, stale=False):
        await self._engine.publish(
            "jev",
            set=set_name,
            answers=r.to_dict() if r else None,
            decision={"action": d.action.value, "reason": d.reason},
            text=text,
            stale=stale,
            speaker=self.s.speaker.name or self.s.speaker.label,
        )

    async def _turn_event(self, event: str, **kw: Any) -> None:
        await self._engine.publish(
            "interaction", event=event, speaker=self.s.speaker.name or self.s.speaker.label, **kw
        )

    async def _contract_turn(self, kind: str, text: str, interrupted: bool = False) -> None:
        """Contract 2 `turn` (A -> B) at the edge, in the COORDINATION.md envelope."""
        stopped = self.s.user.speech_stopped_at
        t_end = int((time.time() - (self._now() - stopped)) * 1000) if stopped and kind == "final" else None
        await self._engine.publish_contract(
            MSG_TURN, Turn(kind=kind, text=text, t_speech_end_ms=t_end, interrupted=interrupted).model_dump()
        )

    # ------------------------------------------------------------------ frame routing
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            await self._on_bot_started()
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._on_bot_stopped()
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            if direction == FrameDirection.DOWNSTREAM:
                await self._on_vad_start()
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if direction == FrameDirection.DOWNSTREAM:
                await self._on_vad_stop()
            await self.push_frame(frame, direction)
        elif isinstance(frame, InterimTranscriptionFrame):
            await self._on_interim(frame)  # consumed: the aggregator doesn't need interims
        elif isinstance(frame, TranscriptionFrame):
            await self._on_final(frame)  # held until the speech becomes a turn
        elif isinstance(frame, (EndFrame, CancelFrame)):
            for name in ("_overlap_task", "_overlap_watchdog", "_overlap_end_task", "_eot_task",
                         "_hold_task", "_final_wait_task", "_thinking_task", "_early_task"):
                self._cancel(name)
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------ bot state
    async def _on_bot_started(self) -> None:
        self.s.bot.speaking = True
        if not self.s.bot.started_at or self.s.phase == Phase.THINKING:
            self.s.bot.started_at = self._now()
        self._cancel("_thinking_task")
        if not self._overlap:
            self._engine.set_phase(Phase.BOT_SPEAKING)
        await self._engine.publish_snapshot(force=True)

    async def _on_bot_stopped(self) -> None:
        self.s.bot.speaking = False
        if self.s.bot.response_done:
            self._awaiting_bot = False
        if self.s.bot.spoken_text:
            self.s.conversation.last_bot_turn = self.s.bot.spoken_text
        if self._overlap and not self._awaiting_bot and self._overlap_resolved != Action.INTERRUPT:
            # The bot's whole response finished while the user was overlapping.
            if self._overlap_resolved == Action.CONTINUE:
                await self._end_overlap(discard=True)  # it was a backchannel; forget it
            else:
                # undecided: from here on it's just a normal utterance
                self._overlap = False
                self._cancel("_overlap_watchdog")
                self._cancel("_overlap_end_task")
                if not self.s.user.vad_speaking and self._held:
                    self._schedule_eot()
        if self.s.phase in (Phase.BOT_SPEAKING, Phase.OVERLAP):
            if self._awaiting_bot:
                self._engine.set_phase(Phase.OVERLAP if self._overlap else Phase.THINKING)
            else:
                self._engine.set_phase(Phase.LISTENING if (self.s.user.vad_speaking or self._held) else Phase.IDLE)
        await self._engine.publish_snapshot(force=True)

    def _bot_busy(self) -> bool:
        return self.s.bot.speaking or self._awaiting_bot

    # ------------------------------------------------------------------ VAD
    async def _on_vad_start(self) -> None:
        if self._overlap and self._overlap_resolved == Action.CONTINUE:
            # the previous overlap was a backchannel that Gradium hasn't flushed yet; keep it out
            # of the new utterance's text
            self._discarded_text = _norm(self._overlap_text())
            self._held.clear()
            self.s.user.partial_transcript = ""
        self._utt += 1
        u = self.s.user
        u.vad_speaking = True
        u.speech_started_at = self._now()
        u.partial_transcript = ""
        # user resumed: any pending end-of-turn decision is void
        self._cancel("_eot_task")
        self._cancel("_hold_task")
        self._cancel("_final_wait_task")

        self._spec = None
        self._cancel("_early_task")
        if self._bot_busy() and not self._turn_open:
            self._start_overlap()
        else:
            self._engine.set_phase(Phase.LISTENING)
        await self._engine.publish("vad", speaking=True)
        await self._engine.publish_snapshot(force=True)

    async def _on_vad_stop(self) -> None:
        u = self.s.user
        u.vad_speaking = False
        u.speech_stopped_at = self._now()
        if self._utt == self._discard_utt and not self._overlap:
            pass  # backchannel already handled
        elif self._overlap:
            # wait for the final transcript; if none arrives it was noise
            self._cancel("_overlap_end_task")
            self._overlap_end_task = self.create_task(self._overlap_timeout(self._utt), "overlap_end")
        else:
            # Speculative Jev end-of-turn on the interim text while Gradium finalizes.
            spec_text = (self._held_text() + " " + u.partial_transcript).strip()
            if spec_text:
                state = to_jev_state(self.s, self._now(), transcript=spec_text)
                task = self.create_task(self._jev.ask(state, END_OF_TURN_QUESTIONS), "jev_eot_spec")
                self._spec = (self._utt, _norm(spec_text), task)
            if self._held and not u.partial_transcript:
                self._schedule_eot()
            else:
                # final transcript not here yet; give it a moment then evaluate
                self._cancel("_final_wait_task")
                self._final_wait_task = self.create_task(self._final_wait(self._utt), "final_wait")
        await self._engine.publish("vad", speaking=False)
        await self._engine.publish_snapshot(force=True)

    async def _final_wait(self, utt: int) -> None:
        await asyncio.sleep(EOT_FINAL_WAIT_S)
        if utt == self._utt and not self._held and not self._overlap:
            if self._turn_open:
                # e.g. interrupted but nothing intelligible followed; keep listening
                self._schedule_eot()
            elif self.s.phase == Phase.LISTENING:
                self._engine.set_phase(Phase.IDLE)

    # ------------------------------------------------------------------ transcripts
    async def _on_interim(self, frame: InterimTranscriptionFrame) -> None:
        text = frame.text
        await self._contract_turn("partial", text)
        if self._discarded_text and _norm(text).startswith(self._discarded_text):
            text = _strip_prefix(text, self._discarded_text)
        self.s.user.partial_transcript = text
        if not text.strip():
            return
        if not self._overlap and self._bot_busy() and not self._turn_open and self._utt != self._discard_utt:
            # VAD missed short speech (e.g. a quiet "yeah") but the ASR heard words
            self._start_overlap()
        if self._overlap:
            if not self.s.user.vad_speaking:
                # no VAD stop will come for this speech; end the overlap if the words stop
                self._cancel("_overlap_end_task")
                self._overlap_end_task = self.create_task(self._overlap_timeout(self._utt), "overlap_end")
            await self._maybe_ask_overlap()
        elif not self.s.user.vad_speaking and self._utt != self._answered_utt and self.s.phase in (Phase.LISTENING, Phase.IDLE):
            # user is silent and the ASR is catching up: decide end-of-turn on the interim text
            self._cancel("_early_task")
            self._early_task = self.create_task(self._early_eot(self._utt), "jev_eot_early")
        await self._engine.publish_snapshot()

    async def _on_final(self, frame: TranscriptionFrame) -> None:
        text = frame.text.strip()
        self.s.user.partial_transcript = ""
        norm = _norm(text)
        if self._discarded_text:
            # text we already consumed (a dropped backchannel, or interim text we answered early)
            d = self._discarded_text
            if norm == d or d.endswith(norm) or (norm.endswith(d) and self._utt == self._answered_utt):
                self._discarded_text = ""
                return
            if norm.startswith(d):
                text = _strip_prefix(text, d)
            else:
                text = _strip_leaked_tail(text, d)
            frame = TranscriptionFrame(text, frame.user_id, frame.timestamp, frame.language)
            self._discarded_text = ""
        if not text:
            return
        if self._utt == self._discard_utt and not self._overlap:
            logger.debug(f"dropping late final of discarded utterance: {text!r}")
            return
        if self._utt == self._answered_utt:
            logger.debug(f"dropping late final of utterance answered from interim: {text!r}")
            return
        self._held.append(frame)
        self._cancel("_final_wait_task")
        await self._engine.publish("transcript", role="user_partial_final", text=text,
                                   speaker=self.s.speaker.name or self.s.speaker.label)
        if self._overlap and self._overlap_resolved != Action.INTERRUPT:
            await self._final_overlap_check()
        elif not self.s.user.vad_speaking:
            self._schedule_eot()

    # ------------------------------------------------------------------ overlap (Jev set A)
    def _start_overlap(self) -> None:
        self._overlap = True
        self._overlap_utt = self._utt
        self._overlap_resolved = None
        self._overlap_started = self._now()
        self._overlap_asked_text = None
        self._engine.set_phase(Phase.OVERLAP)
        self._cancel("_overlap_watchdog")
        self._overlap_watchdog = self.create_task(self._overlap_watch(), "overlap_watchdog")

    def _overlap_text(self) -> str:
        return (self._held_text() + " " + self.s.user.partial_transcript).strip()

    async def _overlap_watch(self) -> None:
        """Deterministic backstop: hard-stop words and the long-overlap cap don't wait for Jev."""
        try:
            while self._overlap and self._overlap_resolved != Action.INTERRUPT:
                await asyncio.sleep(0.1)
                text = self._overlap_text()
                speech_s = self._now() - self._overlap_started
                if is_hard_stop(text):
                    await self._apply_overlap(Decision(Action.INTERRUPT, "hard_stop_phrase"), None, text)
                    return
                if (
                    self._overlap_resolved is None
                    and self.s.user.vad_speaking
                    and speech_s >= self._cfg.overlap_cap_s
                    and len(text.split()) >= self._cfg.overlap_cap_words
                    and self._overlap_task is None
                ):
                    await self._apply_overlap(Decision(Action.INTERRUPT, "overlap_cap"), None, text)
                    return
        except asyncio.CancelledError:
            pass

    async def _maybe_ask_overlap(self, force: bool = False) -> None:
        if not self._overlap or self._overlap_resolved == Action.INTERRUPT:
            return
        text = self._overlap_text()
        if not text or text == self._overlap_asked_text:
            return
        if self._overlap_task is not None:
            self._overlap_dirty = True
            return
        wait = OVERLAP_MIN_INTERVAL_S - (self._now() - self._overlap_last_ask)
        self._overlap_task = self.create_task(self._ask_overlap(max(0.0, wait) if not force else 0.0), "jev_overlap")

    async def _ask_overlap(self, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            utt = self._overlap_utt
            text = self._overlap_text()
            self._overlap_asked_text = text
            self._overlap_last_ask = self._now()
            speech_s = (self._now() - self._overlap_started)
            state = to_jev_state(self.s, self._now(), transcript=text, overlap_ms=int(speech_s * 1000))
            r = await self._jev.ask(state, OVERLAP_QUESTIONS)
            d = decide_overlap(r, text=text, speech_s=speech_s, cfg=self._cfg)
            stale = (not self._overlap) or utt != self._overlap_utt
            await self._publish_jev("overlap", r, d, text, stale=stale)
            if not stale:
                await self._apply_overlap(d, r, text)
        except asyncio.CancelledError:
            return
        finally:
            self._overlap_task = None
        if self._overlap_dirty:
            self._overlap_dirty = False
            await self._maybe_ask_overlap()

    async def _final_overlap_check(self) -> None:
        """User stopped talking during bot speech and the final transcript arrived: decide once more."""
        self._cancel("_overlap_end_task")
        self._cancel("_overlap_task")
        self._overlap_dirty = False
        text = self._held_text()
        speech_s = max(0.0, (self.s.user.speech_stopped_at or self._now()) - self._overlap_started)
        state = to_jev_state(self.s, self._now(), transcript=text, overlap_ms=int(speech_s * 1000))
        r = await self._jev.ask(state, OVERLAP_QUESTIONS)
        d = decide_overlap(r, text=text, speech_s=speech_s, cfg=self._cfg)
        if d.action == Action.WAIT:
            d = Decision(Action.CONTINUE, f"final_{d.reason}")
        await self._publish_jev("overlap_final", r, d, text)
        await self._apply_overlap(d, r, text)
        if d.action != Action.INTERRUPT:
            await self._end_overlap(discard=True)
        elif not self.s.user.vad_speaking:
            self._schedule_eot()

    async def _overlap_timeout(self, utt: int) -> None:
        await asyncio.sleep(OVERLAP_FINAL_WAIT_S)
        if self._overlap and utt == self._utt and self._overlap_resolved != Action.INTERRUPT:
            await self._turn_event("noise", text=self.s.user.partial_transcript)
            await self._end_overlap(discard=True)

    async def _apply_overlap(self, d: Decision, r: JevResult | None, text: str) -> None:
        if d.action == Action.INTERRUPT and self._overlap_resolved != Action.INTERRUPT:
            self._overlap_resolved = Action.INTERRUPT
            await self._interrupt(d.reason, text)
        elif d.action == Action.CONTINUE and self._overlap_resolved is None:
            self._overlap_resolved = Action.CONTINUE
            await self._turn_event("backchannel", text=text, reason=d.reason)

    async def _end_overlap(self, discard: bool) -> None:
        self._overlap = False
        self._cancel("_overlap_watchdog")
        self._cancel("_overlap_end_task")
        if discard:
            discarded = self._overlap_text()
            self._held.clear()
            self.s.user.partial_transcript = ""
            self._discard_utt = self._overlap_utt
            if discarded:
                self._discarded_text = _norm(discarded)
            if not self.s.user.vad_speaking:
                # make Gradium finalize the backchannel now so it doesn't prefix the next utterance
                await self.push_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        if self.s.bot.speaking:
            self._engine.set_phase(Phase.BOT_SPEAKING)
        elif self._awaiting_bot:
            self._engine.set_phase(Phase.THINKING)
        else:
            self._engine.set_phase(Phase.IDLE)
        await self._engine.publish_snapshot(force=True)

    # ------------------------------------------------------------------ actions
    async def _interrupt(self, reason: str, text: str) -> None:
        """Stop the bot: InterruptionFrame downstream cancels LLM, TTS and queued output audio."""
        logger.info(f"INTERRUPT ({reason}): {text!r}")
        self._overlap = False
        self._cancel("_overlap_watchdog")
        self._cancel("_thinking_task")
        self._awaiting_bot = False
        if self.s.bot.spoken_text:
            self.s.conversation.last_bot_turn = self.s.bot.spoken_text + " [interrupted]"
        await self.push_frame(InterruptionFrame())
        self._interrupted_this_turn = True
        await self._open_turn()
        self.s.bot.speaking = False
        self._engine.set_phase(Phase.LISTENING)
        await self._turn_event("interrupt", text=text, reason=reason, interrupted=True)
        await self._engine.publish_snapshot(force=True)

    async def _open_turn(self) -> None:
        if not self._turn_open:
            self._turn_open = True
            await self.push_frame(ProposedUserStartedSpeakingFrame())
            await self._turn_event("user_turn_start")

    def _schedule_eot(self) -> None:
        if self._overlap:
            return
        self._cancel("_eot_task")
        self._eot_task = self.create_task(self._evaluate_eot(self._utt), "jev_eot")

    async def _evaluate_eot(self, utt: int) -> None:
        try:
            text = self._held_text()
            silence_s = self._now() - (self.s.user.speech_stopped_at or self._now())
            r: JevResult | None = None
            spec = self._spec
            if text and spec and spec[0] == utt and spec[1] == _norm(text):
                r = await spec[2]  # speculative answer on identical text: saves a Jev round trip
                if r is not None:
                    r.latency_ms = 0.0 if r.ok else r.latency_ms
            elif text:
                state = to_jev_state(self.s, self._now(), transcript=text)
                r = await self._jev.ask(state, END_OF_TURN_QUESTIONS)
            if utt != self._utt or self.s.user.vad_speaking or utt == self._answered_utt:
                return  # user resumed while we were asking, or already answered from interim
            silence_s = self._now() - (self.s.user.speech_stopped_at or self._now())
            d = decide_end_of_turn(r, text=text, silence_s=silence_s, cfg=self._cfg)
            await self._publish_jev("end_of_turn", r, d, text)
            if d.action == Action.RESPOND:
                await self._respond(text, r)
            elif d.action == Action.DROP:
                self._held.clear()
                await self._turn_event("drop", text=text, reason=d.reason)
                if not self._turn_open:
                    self._engine.set_phase(Phase.IDLE)
            else:  # HOLD
                await self._turn_event("hold", text=text, reason=d.reason)
                self._cancel("_hold_task")
                remaining = max(0.05, self._cfg.hold_max_silence_s - silence_s)
                self._hold_task = self.create_task(self._hold_expire(utt, remaining), "hold")
        except asyncio.CancelledError:
            return

    async def _early_eot(self, utt: int) -> None:
        """Speculative end-of-turn on interim text while the final transcript is still pending."""
        try:
            await asyncio.sleep(0.03)  # coalesce bursts of word fragments
            text = (self._held_text() + " " + self.s.user.partial_transcript).strip()
            if not text:
                return
            state = to_jev_state(self.s, self._now(), transcript=text)
            r = await self._jev.ask(state, END_OF_TURN_QUESTIONS)
            current = (self._held_text() + " " + self.s.user.partial_transcript).strip()
            if utt != self._utt or self.s.user.vad_speaking or self._overlap or _norm(current) != _norm(text):
                return
            self._spec = (utt, _norm(text), _done(r))
            silence_s = self._now() - (self.s.user.speech_stopped_at or self._now())
            d = decide_end_of_turn(r, text=text, silence_s=silence_s, cfg=self._cfg)
            if d.action == Action.RESPOND:
                await self._publish_jev("end_of_turn", r, Decision(d.action, d.reason + "@interim"), text)
                self._answered_utt = utt
                self._cancel("_final_wait_task")
                # the interim part isn't finalized by Gradium yet; strip it if it leaks later
                self._discarded_text = _norm(self.s.user.partial_transcript)
                await self._respond(text, r)
        except asyncio.CancelledError:
            return

    async def _hold_expire(self, utt: int, delay: float) -> None:
        await asyncio.sleep(delay)
        if utt != self._utt or self.s.user.vad_speaking or utt == self._answered_utt:
            return
        text = self._held_text()
        if not text:
            return
        d = decide_end_of_turn(None, text=text, silence_s=self._cfg.hold_max_silence_s, cfg=self._cfg)
        await self._publish_jev("end_of_turn", None, d, text)
        await self._respond(text, None)

    async def _respond(self, text: str, r: JevResult | None) -> None:
        """Close the user turn: release held transcripts then ProposedUserStoppedSpeakingFrame."""
        speaker = self.s.speaker.label
        if self._before_respond:
            try:
                text_for_llm = await self._before_respond(text, speaker)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"before_respond failed: {e}")
                text_for_llm = text
        else:
            text_for_llm = text
        self._answered_utt = self._utt
        held, self._held = self._held, []
        for name in ("_early_task", "_eot_task", "_hold_task"):
            if asyncio.current_task() is not getattr(self, name):
                self._cancel(name)
        await self._open_turn()
        if held:
            first = held[0]
            await self.push_frame(
                TranscriptionFrame(text_for_llm, first.user_id, first.timestamp, first.language)
            )
        else:
            await self.push_frame(TranscriptionFrame(text_for_llm, "user", ""))
        await self.push_frame(ProposedUserStoppedSpeakingFrame())
        self._turn_open = False
        c = self.s.conversation
        c.last_user_turn = text
        c.turn_count += 1
        self.s.bot.spoken_text = ""
        self.s.bot.current_sentence = ""
        self.s.bot.response_done = False
        self._awaiting_bot = True
        self._engine.set_phase(Phase.THINKING)
        self._cancel("_thinking_task")
        self._thinking_task = self.create_task(self._thinking_watch(), "thinking_watch")
        await self._turn_event("user_turn_end", text=text)
        await self._contract_turn("final", text, interrupted=self._interrupted_this_turn)
        self._interrupted_this_turn = False
        await self._engine.publish("transcript", role="user", text=text,
                                   speaker=self.s.speaker.name or self.s.speaker.label)
        if self._on_turn_accepted:
            self.create_task(self._on_turn_accepted(text, speaker, r), "turn_accepted")
        await self._engine.publish_snapshot(force=True)

    async def _thinking_watch(self) -> None:
        """Safety net: if no bot audio ever arrives, don't stay 'busy' forever."""
        try:
            await asyncio.sleep(THINKING_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._awaiting_bot and not self.s.bot.speaking:
            logger.warning("no bot audio after respond; back to idle")
            self._awaiting_bot = False
            if self.s.phase == Phase.THINKING:
                self._engine.set_phase(Phase.IDLE)

    async def on_response_done(self) -> None:
        """Called by BotTap when the LLM response end frame has passed the output transport."""
        self.s.bot.response_done = True
        if not self.s.bot.speaking and self._awaiting_bot:
            self._awaiting_bot = False
            if self.s.phase == Phase.THINKING:
                self._engine.set_phase(Phase.IDLE)


__all__ = ["InteractionController", "is_introduction"]
