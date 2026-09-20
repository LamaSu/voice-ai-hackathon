"""Background agents: work the conversation shouldn't wait for.

"Set a timer for 10 seconds", "what's Apple trading at", "what was the score" — each spins
an agent that runs off the conversation path. The agent acknowledges immediately, the user
keeps talking, and when the agent finishes its answer is spoken at the next natural gap.

Jev decides whether a turn is a background task (the `task` question in the end-of-turn
fan-out); this module runs it and publishes its state to the telemetry widget.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

KINDS = ("timer", "stock", "sports", "lookup")
MAX_ACTIVE = 6
KEEP_FINISHED = 8
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90,
}
_UNITS = {"second": 1, "sec": 1, "secs": 1, "seconds": 1, "minute": 60, "min": 60,
          "mins": 60, "minutes": 60, "hour": 3600, "hours": 3600}
_DURATION = re.compile(
    r"(\d+|" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True)) + r")\s*"
    r"(seconds?|secs?|minutes?|mins?|hours?)\b",
    re.IGNORECASE,
)


def parse_duration(text: str) -> float | None:
    """Total seconds in a phrase like "10 seconds" or "one minute thirty seconds"."""
    total = 0.0
    for amount, unit in _DURATION.findall(text):
        a = amount.lower()
        n = float(a) if a.isdigit() else float(_WORD_NUMBERS.get(a, 0))
        total += n * _UNITS.get(unit.lower(), 0)
    return total or None


@dataclass
class TaskRecord:
    id: str
    kind: str
    title: str
    request: str
    started_at: float = field(default_factory=time.time)
    status: str = "running"  # running | done | failed
    result: str | None = None
    error: str | None = None
    ended_at: float | None = None

    @property
    def elapsed_s(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def to_ui(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "started_at": round(self.started_at, 3),
            "elapsed_s": round(self.elapsed_s, 1),
            "result": self.result,
            "error": self.error,
        }


class TaskRunner:
    """Spawns and tracks background agents; announces results when they finish."""

    def __init__(
        self,
        publish: Callable[[list[dict]], Awaitable[None]],
        announce: Callable[[str], Awaitable[None]],
        *,
        llm: Any = None,
        clock: Callable[[], float] = time.time,
    ):
        self._publish = publish
        self._announce = announce
        self._llm = llm  # MemoryLLM-shaped: _json(system, user) for parameter extraction
        self._clock = clock
        self.active: dict[str, TaskRecord] = {}
        self.finished: list[TaskRecord] = []
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- lifecycle
    def snapshot(self) -> list[dict[str, Any]]:
        return [r.to_ui() for r in list(self.active.values()) + list(reversed(self.finished))]

    async def _changed(self) -> None:
        await self._publish(self.snapshot())

    async def start(self, kind: str, request: str) -> TaskRecord | None:
        """Create and spawn an agent. Returns immediately: the conversation must not wait."""
        if kind not in KINDS or len(self.active) >= MAX_ACTIVE:
            return None
        rec = TaskRecord(id=uuid.uuid4().hex[:8], kind=kind, title=self._title(kind, request), request=request)
        rec.started_at = self._clock()
        self.active[rec.id] = rec
        logger.info(f"agent {rec.id} [{kind}]: {rec.title}")
        task = asyncio.create_task(self._run(rec))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await self._changed()
        return rec

    def _title(self, kind: str, request: str) -> str:
        if kind == "timer":
            secs = parse_duration(request)
            return f"Timer · {self._humanize(secs)}" if secs else "Timer"
        short = request.strip().rstrip(".?")
        return f"{kind.capitalize()} · {short[:40]}"

    @staticmethod
    def _humanize(seconds: float | None) -> str:
        if not seconds:
            return "?"
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            m, s = divmod(int(seconds), 60)
            return f"{m}m" + (f" {s}s" if s else "")
        h, m = divmod(int(seconds) // 60, 60)
        return f"{h}h" + (f" {m}m" if m else "")

    async def _run(self, rec: TaskRecord) -> None:
        handler = {
            "timer": self._run_timer,
            "stock": self._run_stock,
            "sports": self._run_sports,
            "lookup": self._run_lookup,
        }[rec.kind]
        try:
            rec.result = await handler(rec)
            rec.status = "done"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a failed agent reports, it doesn't crash the call
            logger.warning(f"agent {rec.id} [{rec.kind}] failed: {e}")
            rec.status = "failed"
            rec.error = str(e)
            rec.result = f"I couldn't finish that one — {type(e).__name__}."
        rec.ended_at = self._clock()
        self.active.pop(rec.id, None)
        self.finished.append(rec)
        del self.finished[:-KEEP_FINISHED]
        await self._changed()
        if rec.result:
            await self._announce(rec.result)

    # ---------------------------------------------------------------- handlers
    async def _run_timer(self, rec: TaskRecord) -> str:
        seconds = parse_duration(rec.request) or await self._extract_seconds(rec.request) or 60.0
        rec.title = f"Timer · {self._humanize(seconds)}"
        await self._changed()
        await asyncio.sleep(seconds)
        return f"Your {self._humanize(seconds)} timer is up."

    async def _run_stock(self, rec: TaskRecord) -> str:
        symbol = await self._extract(rec.request, "ticker") or "AAPL"
        symbol = symbol.upper().strip()
        rec.title = f"Stock · {symbol}"
        await self._changed()
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as http:
            r = await http.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "5d"},
            )
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        name = meta.get("shortName") or symbol
        if price is None:
            return f"I couldn't get a price for {symbol}."
        move = ""
        if prev:
            pct = (price - prev) / prev * 100
            move = f", {'up' if pct >= 0 else 'down'} {abs(pct):.1f} percent"
        return f"{name} is at {price:,.2f} {meta.get('currency', '')}{move}."

    async def _run_sports(self, rec: TaskRecord) -> str:
        league = (await self._extract(rec.request, "league") or "nba").lower().strip()
        paths = {
            "nba": "basketball/nba", "nfl": "football/nfl", "mlb": "baseball/mlb",
            "nhl": "hockey/nhl", "ncaaf": "football/college-football",
            "epl": "soccer/eng.1", "premier league": "soccer/eng.1", "soccer": "soccer/eng.1",
        }
        path = paths.get(league, "basketball/nba")
        rec.title = f"Scores · {league.upper()}"
        await self._changed()
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as http:
            r = await http.get(f"https://site.web.api.espn.com/apis/site/v2/sports/{path}/scoreboard")
            r.raise_for_status()
            events = r.json().get("events", [])
        if not events:
            return f"There are no {league.upper()} games on the board right now."
        lines = []
        for e in events[:3]:
            comp = e["competitions"][0]
            teams = " ".join(
                f"{c['team'].get('abbreviation', c['team'].get('displayName'))} {c.get('score', '')}".strip()
                for c in comp["competitors"]
            )
            lines.append(f"{teams} ({comp['status']['type'].get('shortDetail', '')})")
        return "Here's what I found: " + "; ".join(lines) + "."

    async def _run_lookup(self, rec: TaskRecord) -> str:
        if not self._llm:
            return "I don't have a way to look that up right now."
        data = await self._llm._json(  # noqa: SLF001 - same package-internal helper as memory
            "Answer the user's question in one spoken sentence, under 30 words. "
            'Reply as JSON {"answer": string}. If you can\'t know it (live data, recent events), '
            "say so plainly in the answer.",
            rec.request,
            max_tokens=120,
        )
        return str(data.get("answer") or "I couldn't find an answer for that.")

    # ---------------------------------------------------------------- extraction
    async def _extract(self, request: str, field_name: str) -> str | None:
        if not self._llm:
            return None
        try:
            data = await self._llm._json(  # noqa: SLF001
                f'Extract the {field_name} the user is asking about. Reply JSON '
                f'{{"{field_name}": string|null}}. For a ticker use the stock symbol '
                "(Apple -> AAPL). For a league use one of nba, nfl, mlb, nhl, epl.",
                request,
                max_tokens=40,
            )
            value = data.get(field_name)
            return str(value) if value else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"extraction of {field_name} failed: {e}")
            return None

    async def _extract_seconds(self, request: str) -> float | None:
        raw = await self._extract(request, "seconds")
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    async def cancel_all(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        self._tasks.clear()


def parse_task_json(text: str) -> dict[str, Any]:
    """Small helper used by tests and the extraction path."""
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return json.loads(m.group(0)) if m else {}
