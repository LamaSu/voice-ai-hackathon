"""Local streaming STT: NVIDIA Parakeet TDT 0.6B v3 on Apple Silicon via MLX.

Why local: barge-in latency is dominated by how soon the first word of the user's
interrupting speech reaches Jev. Gradium's streaming decoder runs ~0.9 s behind the
speaker (its `delay_in_frames` lookahead); Parakeet streaming on an M4 emits text
~0.13 s after each chunk is fed, so a 0.24 s chunk puts words in front of Jev in
~0.4 s instead of ~1.0 s.

Shape: audio chunks are queued and transcribed by one worker task (MLX calls are
serialized and run off the event loop), which pushes InterimTranscriptionFrames as the
text grows. A VAD stop flushes the tail and pushes the final TranscriptionFrame, then
the stream is reset so the next utterance starts with a clean context.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "parakeet-tdt-0.6b-v3"
CHUNK_S = 0.2  # 0.16 saturates the GPU (RTF 0.85) and loses accuracy; 0.2 gives RTF ~0.65
CONTEXT = (256, 256)
FLUSH_PAD_S = 0.12  # enough for the decoder to finish the last word, no more
PREROLL_S = 0.3  # audio kept before VAD fires, so the first syllable isn't lost
_FLUSH = object()


# MLX state (streams, KV caches, arrays) is thread-local, so the model must be LOADED and
# USED on the same thread. One process-wide worker owns all of it; inference is serialized
# there (RTF ~0.55 per stream, so a couple of concurrent calls still fit).
_MLX = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


def run_mlx_sync(fn, *args):
    return _MLX.submit(fn, *args).result()


async def run_mlx(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_MLX, fn, *args)


def _load(model_dir: Path | str):
    from parakeet_mlx import from_pretrained

    t0 = time.perf_counter()
    model = from_pretrained(str(model_dir))
    logger.info(f"parakeet: loaded {Path(model_dir).name} in {time.perf_counter() - t0:.1f}s")
    return model


def load_model(model_dir: Path | str = DEFAULT_MODEL_DIR):
    """Load the MLX Parakeet model (~2.5 GB) on the MLX thread. Once per process."""
    return run_mlx_sync(_load, model_dir)


class ParakeetSTTService(STTService):
    """Streaming local ASR. Emits interim transcripts continuously and a final on VAD stop."""

    def __init__(
        self,
        *,
        model: Any,
        chunk_s: float = CHUNK_S,
        language: Language = Language.EN,
        sample_rate: int | None = 16000,
        **kwargs,
    ):
        kwargs.setdefault(
            "settings", STTSettings(model="parakeet-tdt-0.6b-v3", language=language)
        )
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._model = model
        self._chunk_s = chunk_s
        self._language = language
        self._buf = bytearray()
        self._stream = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._last_text = ""
        self._user_id = ""
        # Fed only while VAD hears speech: on silence the model invents fillers
        # ("Uh", "Oh", "Mm"), and a hallucinated word is enough to trigger a barge-in.
        self._speaking = False
        self._preroll = bytearray()

    async def _in_worker(self, fn, *args):
        """Run an MLX call on the process-wide MLX thread."""
        return await run_mlx(fn, *args)

    # ---------------------------------------------------------------- lifecycle
    def _open_stream(self):
        self._close_stream()
        self._stream = self._model.transcribe_stream(context_size=CONTEXT)
        self._stream.__enter__()
        self._last_text = ""

    def _close_stream(self):
        if self._stream is not None:
            try:
                self._stream.__exit__(None, None, None)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"parakeet: stream close: {e}")
            self._stream = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._in_worker(self._open_stream)
        # first inference compiles kernels (~2.5s); pay it now, not on the user's first word
        await self._in_worker(self._add_audio, np.zeros(int(0.5 * self.sample_rate), dtype=np.float32))
        await self._in_worker(self._open_stream)
        if not self._worker:
            self._worker = self.create_task(self._run_worker(), "parakeet_worker")
        logger.info(f"parakeet: ready (chunk {self._chunk_s}s, {self.sample_rate} Hz)")

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._shutdown()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._shutdown()

    async def _shutdown(self):
        if self._worker:
            await self.cancel_task(self._worker)
            self._worker = None
        await self._in_worker(self._close_stream)

    # ---------------------------------------------------------------- inference
    def _add_audio(self, samples: np.ndarray) -> str:
        import mlx.core as mx

        self._stream.add_audio(mx.array(samples))
        return self._stream.result.text.strip()

    async def _run_worker(self):
        while True:
            item = await self._queue.get()
            try:
                if item is _FLUSH:
                    await self._finalize()
                    continue
                text = await self._in_worker(self._add_audio, item)
                if text and text != self._last_text:
                    self._last_text = text
                    await self.push_frame(
                        InterimTranscriptionFrame(text, self._user_id, time_now_iso8601(), self._language)
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"parakeet: chunk failed: {e}")

    async def _finalize(self):
        tail = np.frombuffer(bytes(self._buf), dtype=np.int16).astype(np.float32) / 32768.0
        self._buf.clear()
        pad = np.zeros(int(FLUSH_PAD_S * self.sample_rate), dtype=np.float32)
        try:
            text = await self._in_worker(self._add_audio, np.concatenate([tail, pad]))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"parakeet: flush failed: {e}")
            text = self._last_text
        if text:
            logger.debug(f"parakeet final: [{text}]")
            await self.emit_stt_usage_metrics()
            await self.push_frame(
                TranscriptionFrame(text, self._user_id, time_now_iso8601(), self._language)
            )
        # fresh context per utterance: nothing from this turn can leak into the next one
        await self._in_worker(self._open_stream)

    # ---------------------------------------------------------------- frames
    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Buffer audio and hand whole chunks to the worker (transcripts are pushed there)."""
        if not self._speaking:
            # keep a rolling pre-roll so the first syllable survives the VAD's start delay
            self._preroll.extend(audio)
            preroll_bytes = int(PREROLL_S * self.sample_rate) * 2
            if len(self._preroll) > preroll_bytes:
                del self._preroll[: len(self._preroll) - preroll_bytes]
            yield None
            return
        self._buf.extend(audio)
        chunk_bytes = int(self._chunk_s * self.sample_rate) * 2
        while len(self._buf) >= chunk_bytes:
            chunk = bytes(self._buf[:chunk_bytes])
            del self._buf[:chunk_bytes]
            self._queue.put_nowait(np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0)
        yield None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._speaking = True
            self._buf.extend(self._preroll)
            self._preroll.clear()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speaking = False
            self._queue.put_nowait(_FLUSH)
