"""Contract 4 (`metrics`) — the feed behind the latency HUD.

Lane D owns this. It turns Pipecat's own latency instrumentation into the
one number the judges will ask about: **end of speech to first audio**.

`UserBotLatencyObserver` measures exactly that interval — from
`VADUserStoppedSpeakingFrame` to `BotStartedSpeakingFrame` — and, with
`enable_metrics=True`, also breaks it down per stage (endpointing wait,
transcription, LLM inference, speech synthesis). We forward both to the
browser as an RTVI server message so the HUD shows real numbers rather
than a stopwatch guess, and append every sample to a metrics log so a
speech-path PR can quote before/after figures (CLAUDE.md rule 8).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from app.contracts import MSG_METRICS, Metrics

DEFAULT_METRICS_LOG = Path(__file__).resolve().parents[3] / "metrics.jsonl"


class LatencyHUD:
    """Collects latency samples and publishes them as Contract 4 `metrics`."""

    def __init__(
        self,
        send: Callable[[dict[str, Any]], Any],
        *,
        log_path: Path | None = DEFAULT_METRICS_LOG,
    ):
        """Set up the HUD feed.

        Args:
            send: Awaitable that ships one RTVI server message to the client.
            log_path: Where to append samples as JSON lines. None disables
                the log (used by tests).
        """
        self._send = send
        self._log_path = log_path
        self._samples_ms: list[float] = []
        self._content_ms: list[float] = []
        self._content = None

    @property
    def samples_ms(self) -> list[float]:
        """Every end-of-speech-to-first-audio measurement so far, in ms."""
        return list(self._samples_ms)

    def median_ms(self) -> float | None:
        """Median end-of-speech-to-first-audio, or None before the first turn.

        The demo checklist asks for the median, not the mean: one cold
        start should not decide the number we put on screen.
        """
        if not self._samples_ms:
            return None
        ordered = sorted(self._samples_ms)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def median_content_ms(self) -> float | None:
        """Median end-of-speech-to-answer, the number that survives a filler."""
        if not self._content_ms:
            return None
        ordered = sorted(self._content_ms)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def attach(self, observer, content_observer=None) -> None:
        """Subscribe to a `UserBotLatencyObserver`.

        Pass a `ContentLatencyObserver` as well and each sample also carries
        time to the first answer audio, so a cached filler cannot quietly
        turn the headline number into something else.
        """
        self._content = content_observer

        @observer.event_handler("on_latency_breakdown")
        async def _on_breakdown(_observer, breakdown):
            await self.record(
                total_secs=breakdown.total_secs,
                # `key` is the stable identifier — it survives a label rewording,
                # so the HUD keeps grouping stages correctly across Pipecat bumps.
                stages={
                    c.key: round(c.duration_secs * 1000, 1)
                    for c in getattr(breakdown, "contributions", [])
                },
                content_secs=(
                    (self._content.take() or 0) / 1000 if self._content is not None else None
                ),
                detail={
                    "measured_from": str(getattr(breakdown, "measured_from", "") or ""),
                    "labels": {
                        c.key: f"{c.label} [{c.owner}]"
                        for c in getattr(breakdown, "contributions", [])
                    },
                    "ttfb": {
                        m.processor: round(m.duration_secs * 1000, 1) for m in breakdown.ttfb
                    },
                },
            )

    async def record(
        self,
        *,
        total_secs: float,
        stages: dict[str, float] | None = None,
        detail: dict[str, Any] | None = None,
        content_secs: float | None = None,
        filler: str | None = None,
    ) -> Metrics:
        """Record one sample, publish it, and append it to the metrics log."""
        total_ms = round(total_secs * 1000, 1)
        self._samples_ms.append(total_ms)
        # With no filler the answer *is* the first audio, so the two coincide.
        content_ms = round(content_secs * 1000, 1) if content_secs else total_ms
        self._content_ms.append(content_ms)

        sample = Metrics(
            t_ms=int(time.time() * 1000),
            end_of_speech_to_first_audio_ms=total_ms,
            end_of_speech_to_first_content_ms=content_ms,
            filler=filler,
            stages=stages or {},
            detail={
                **(detail or {}),
                "median_ms": self.median_ms(),
                "median_content_ms": self.median_content_ms(),
                "n": len(self._samples_ms),
            },
        )

        if content_ms != total_ms:
            logger.info(
                f"end of speech → first audio {total_ms} ms (filler), "
                f"→ answer {content_ms} ms (median {self.median_ms()} ms)"
            )
        else:
            logger.info(f"end of speech → first audio: {total_ms} ms (median {self.median_ms()} ms)")
        self._append_to_log(sample)
        await self._send({"type": MSG_METRICS, "payload": sample.model_dump()})
        return sample

    def _append_to_log(self, sample: Metrics) -> None:
        if self._log_path is None:
            return
        try:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample.model_dump()) + "\n")
        except OSError as exc:
            # A HUD that cannot write its log still has a demo to run.
            logger.warning(f"could not append to {self._log_path}: {exc}")
