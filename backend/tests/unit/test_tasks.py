"""Background agents: lifecycle, telemetry and the rules for speaking a result."""

from __future__ import annotations

import asyncio

import pytest

from app.jev.client import JevResult
from app.tasks.runner import TaskRunner, parse_duration
from app.turns.policy import choose_task


@pytest.mark.parametrize(
    "text,expected",
    [
        ("set a timer for 10 seconds", 10.0),
        ("timer for five minutes", 300.0),
        ("remind me in 1 minute 30 seconds", 90.0),
        ("two hours please", 7200.0),
        ("what's the score", None),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def task_result(choice, confidence=0.9):
    return JevResult(choices={"task": {"choice": choice, "confidence": confidence, "probabilities": {}}})


@pytest.mark.parametrize(
    "result,expected",
    [
        (task_result("timer"), "timer"),
        (task_result("stock"), "stock"),
        (task_result("none"), None),
        (task_result("timer", 0.4), None),  # too unsure to spin a visible agent
        (None, None),
        (JevResult(ok=False, error="timeout"), None),
    ],
)
def test_choose_task(result, expected):
    assert choose_task(result) == expected


async def make_runner():
    published: list[list[dict]] = []
    spoken: list[str] = []

    async def publish(tasks):
        published.append(tasks)

    async def announce(text):
        spoken.append(text)

    return TaskRunner(publish, announce), published, spoken


async def test_timer_agent_runs_in_background_and_announces():
    runner, published, spoken = await make_runner()
    rec = await runner.start("timer", "set a timer for 1 second")
    assert rec and rec.status == "running"
    assert runner.snapshot()[0]["status"] == "running"  # visible in telemetry immediately
    assert not spoken, "the conversation must not wait for the agent"

    await asyncio.sleep(1.4)
    assert rec.status == "done"
    assert spoken == ["Your 1s timer is up."]
    done = runner.snapshot()[0]
    assert done["status"] == "done" and done["elapsed_s"] >= 1.0


async def test_failing_agent_reports_instead_of_crashing():
    runner, published, spoken = await make_runner()

    async def boom(rec):
        raise RuntimeError("no network")

    runner._run_stock = boom  # noqa: SLF001
    rec = await runner.start("stock", "what's Apple at")
    await asyncio.sleep(0.2)
    assert rec.status == "failed"
    assert spoken and "couldn't finish" in spoken[0]
    assert runner.snapshot()[0]["error"] == "no network"


async def test_unknown_kind_and_overload_are_refused():
    runner, _, _ = await make_runner()
    assert await runner.start("teleport", "do a thing") is None
    for i in range(10):
        await runner.start("timer", "timer for 30 seconds")
    assert len(runner.active) <= 6
    await runner.cancel_all()


async def test_telemetry_snapshot_shape():
    runner, _, _ = await make_runner()
    await runner.start("timer", "set a timer for 30 seconds")
    row = runner.snapshot()[0]
    assert set(row) == {"id", "kind", "title", "status", "started_at", "elapsed_s", "result", "error"}
    assert row["title"] == "Timer · 30s"
    await runner.cancel_all()
