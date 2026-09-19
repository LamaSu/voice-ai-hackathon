"""Thin async Jev wrapper: hard timeout, normalized answers, latency metrics."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy


@dataclass
class JevResult:
    """Normalized answers.

    nouls:   {name: probability_yes}
    choices: {name: {"choice": str, "confidence": float, "probabilities": {label: p}}}
    """

    nouls: dict[str, float] = field(default_factory=dict)
    choices: dict[str, dict[str, Any]] = field(default_factory=dict)
    latency_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nouls": self.nouls,
            "choices": self.choices,
            "latency_ms": round(self.latency_ms),
            "ok": self.ok,
            "error": self.error,
        }


class JevLike(Protocol):
    async def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResult: ...


def normalize_answers(answers: dict[str, Any]) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    nouls: dict[str, float] = {}
    choices: dict[str, dict[str, Any]] = {}
    for name, ans in answers.items():
        a = ans.model_dump() if hasattr(ans, "model_dump") else dict(ans)
        if a.get("type") == "noul":
            nouls[name] = float(a["noul"])
        elif a.get("type") == "choice":
            choices[name] = {
                "choice": a["choice"],
                "confidence": float(a.get("confidence") or 0.0),
                "probabilities": {k: round(float(v), 3) for k, v in (a.get("probabilities") or {}).items()},
            }
    return nouls, choices


class JevClient:
    def __init__(self, api_key: str, model: str = "jev-latest", timeout_s: float = 0.6):
        self._timeout_s = timeout_s
        self._client = AsyncTypeSafeClient(
            api_key=api_key,
            model=model,
            retry=RetryPolicy(max_retries=0),
            timeout=max(timeout_s, 1.0),
        )

    async def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResult:
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self._client.system_one(state, questions), timeout=self._timeout_s
            )
        except Exception as e:  # noqa: BLE001 - callers fall back to deterministic rules
            ms = (time.perf_counter() - t0) * 1000
            err = "timeout" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
            logger.warning(f"Jev call failed after {ms:.0f} ms: {err}")
            return JevResult(latency_ms=ms, ok=False, error=err)
        nouls, choices = normalize_answers(resp.answers)
        return JevResult(
            nouls=nouls,
            choices=choices,
            latency_ms=(time.perf_counter() - t0) * 1000,
            model=resp.model,
        )

    async def warmup(self) -> None:
        from typesafe_sdk import Noul

        await self.ask({"ping": True}, {"ok": Noul(instructions="Is this a ping?")})

    async def aclose(self) -> None:
        await self._client.aclose()


class NullJev:
    """Used when no Jev key is configured: every question is unanswered, so the policy's
    deterministic fallbacks (hard-stop words, overlap cap, punctuation/silence) decide."""

    async def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResult:
        return JevResult(ok=False, error="jev_disabled")

    async def warmup(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
