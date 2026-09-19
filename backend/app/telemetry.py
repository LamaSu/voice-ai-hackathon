"""Session telemetry: one file that explains what happened, after the fact.

The existing session log captures `StateEngine` events, which is a good base but
leaves four holes that matter when something breaks five minutes before a demo:

1. **No context.** A log of events is uninterpretable a day later if you don't
   know which model, which STT, which thresholds and which commit produced it.
   Every session now opens with a `session_start` record holding exactly that.
2. **Latency lived elsewhere.** Contract 4 samples went only to `metrics.jsonl`,
   so a slow turn could not be read next to the Jev decision that caused it.
   They are mirrored into the session log too.
3. **Errors vanished.** Warnings and exceptions went to stdout and died with the
   terminal. A loguru sink now copies WARNING and above into the log.
4. **The browser was invisible.** Autoplay blocks, MediaPipe failures and WebRTC
   trouble happen client-side and never reached disk. `client_log` messages from
   the page land in the same file.

Everything is best-effort: telemetry must never be the reason a demo fails, so
every write is guarded and failures degrade to a warning.

**Secrets never enter this file.** Keys are recorded as presence booleans only.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

from loguru import logger

# Anything matching these is never written, even if it reaches a payload.
_SECRET_HINTS = ("api_key", "apikey", "token", "secret", "password", "authorization")
MAX_STRING = 2000


def _redact(value: Any, key: str = "") -> Any:
    """Drop anything that looks like a credential; truncate runaway strings."""
    if any(h in key.lower() for h in _SECRET_HINTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value[:200]]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + f"...<+{len(value) - MAX_STRING} chars>"
    return value


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=Path(__file__).resolve().parents[2],
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, cwd=Path(__file__).resolve().parents[2],
        )
        sha = out.stdout.strip() or "unknown"
        return f"{sha}-dirty" if dirty.stdout.strip() else sha
    except Exception:  # noqa: BLE001 - never block a session on git
        return "unknown"


def environment_snapshot(settings: Any) -> dict[str, Any]:
    """Everything needed to interpret this log later. Keys as booleans only."""
    import pipecat

    snap: dict[str, Any] = {
        "git_sha": _git_sha(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "pipecat": getattr(pipecat, "__version__", "unknown"),
        "pid": os.getpid(),
        # Presence only. Never the value. (CLAUDE.md rule 10.)
        "has_gradium_key": bool(getattr(settings, "gradium_api_key", "")),
        "has_general_compute_key": bool(getattr(settings, "general_compute_api_key", "")),
        "has_jev_key": bool(getattr(settings, "jev_api_key", "")),
    }
    # Record every non-secret setting, so threshold changes are visible in the log.
    for field in (
        "llm_model", "general_compute_base_url", "jev_model", "jev_timeout_s",
        "gradium_tts_voice", "enable_speaker_id", "enable_fillers", "stt_engine",
        "host", "port",
    ):
        if hasattr(settings, field):
            snap[field] = getattr(settings, field)
    return snap


class SessionTelemetry:
    """Append-only JSONL recorder for one session."""

    def __init__(self, session_id: str, log_dir: Path, settings: Any = None):
        self.session_id = session_id
        try:
            self.path = log_dir / f"session-{session_id}.jsonl"
        except Exception:  # noqa: BLE001 - a malformed log_dir must not raise here
            self.path = Path(f"session-{session_id}.jsonl")
        self.started = time.time()
        self.turn = 0
        self._counts: dict[str, int] = {}
        self._turn_open = False
        self._fh = None
        self._sink_id = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a")
        except Exception as e:  # noqa: BLE001 - incl. ValueError on a malformed path
            # Deliberately broad: telemetry must never be the reason a demo dies.
            logger.warning(f"telemetry: cannot open {self.path}: {e}")
            return
        self.write("session_start", **(environment_snapshot(settings) if settings else {}))
        self._install_log_sink()

    # ------------------------------------------------------------------ writing

    def write(self, kind: str, /, **payload: Any) -> None:
        """Append one record. Never raises.

        ``kind`` is positional-only on purpose: real events carry their own
        ``kind`` field — every Jev decision does — and without the ``/`` that
        collides with this parameter and raises. It did, on the first test
        against realistic data.
        """
        if self._fh is None or self._fh.closed:
            return
        self._counts[kind] = self._counts.get(kind, 0) + 1
        record = {
            "wall": round(time.time(), 3),
            "t": round(time.time() - self.started, 3),
            "session": self.session_id,
            "turn": self.turn,
            "type": kind,
            **_redact(payload),
        }
        try:
            self._fh.write(json.dumps(record, default=str) + "\n")
            self._fh.flush()  # a crash must not lose the record explaining it
        except Exception as e:  # noqa: BLE001 - telemetry never breaks a demo
            logger.warning(f"telemetry write failed: {e}")

    def publisher(self):
        """A `StateEngine` publisher that mirrors events into this log."""

        async def write_event(event: dict[str, Any]) -> None:
            kind = event.get("type", "event")
            if kind == "state":
                return  # ~10 Hz snapshots would drown the file
            # Increment *before* writing, not after. The user's words and the
            # Jev decision about them both arrive before the turn is accepted,
            # so incrementing afterwards filed each utterance under the
            # previous turn — which is exactly backwards for reading a log.
            if kind == "transcript" and not self._turn_open:
                self.turn += 1
                self._turn_open = True
            elif kind == "interaction" and event.get("event") == "turn_accepted":
                self._turn_open = False
            self.write(kind, **{k: v for k, v in event.items() if k != "type"})

        return write_event

    def metrics_publisher(self):
        """Mirror Contract 4 samples here too, so latency sits beside its causes."""

        async def write_metrics(message: dict[str, Any]) -> None:
            if isinstance(message, dict) and message.get("type") == "metrics":
                self.write("metrics", **(message.get("payload") or {}))

        return write_metrics

    def client_log(self, payload: dict[str, Any]) -> None:
        """A `client_log` message from the browser (errors, autoplay, WebRTC)."""
        self.write("client", **payload)

    # ------------------------------------------------------------------ plumbing

    def _install_log_sink(self) -> None:
        """Copy WARNING and above into the log; stdout dies with the terminal."""

        def sink(message) -> None:
            rec = message.record
            self.write(
                "log",
                level=rec["level"].name,
                where=f"{rec['name']}:{rec['function']}:{rec['line']}",
                message=rec["message"],
                exception=str(rec["exception"].value) if rec["exception"] else None,
            )

        try:
            self._sink_id = logger.add(sink, level="WARNING", enqueue=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telemetry: could not attach log sink: {e}")

    def close(self) -> None:
        self.write("session_end", duration_s=round(time.time() - self.started, 1),
                   turns=self.turn, counts=dict(self._counts))
        if self._sink_id is not None:
            try:
                logger.remove(self._sink_id)
            except Exception:  # noqa: BLE001
                pass
        if self._fh and not self._fh.closed:
            self._fh.close()
