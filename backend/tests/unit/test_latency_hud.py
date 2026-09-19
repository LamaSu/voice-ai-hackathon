"""Contract 4 (`metrics`) — what the latency HUD is fed. Lane D."""

from __future__ import annotations

import json

import pytest

from app.contracts import MSG_METRICS, Metrics
from app.observers.latency_hud import LatencyHUD


@pytest.fixture
def sent():
    return []


@pytest.fixture
def hud(sent, tmp_path):
    async def send(message):
        sent.append(message)

    return LatencyHUD(send, log_path=tmp_path / "metrics.jsonl")


async def test_records_and_publishes_one_sample(hud, sent):
    await hud.record(total_secs=0.412, stages={"llm inference": 210.0})

    assert len(sent) == 1
    assert sent[0]["type"] == MSG_METRICS
    payload = sent[0]["payload"]
    assert payload["end_of_speech_to_first_audio_ms"] == 412.0
    assert payload["stages"] == {"llm inference": 210.0}
    # The payload must survive a round trip through the contract model.
    assert Metrics(**payload).end_of_speech_to_first_audio_ms == 412.0


async def test_median_is_not_dragged_by_a_cold_start(hud):
    # A slow first turn is normal (connection setup, model warmup). The demo
    # quotes the median precisely so one cold start cannot define the number.
    for secs in (3.5, 0.40, 0.44, 0.38, 0.42):
        await hud.record(total_secs=secs)

    assert hud.median_ms() == 420.0
    assert max(hud.samples_ms) == 3500.0


async def test_median_averages_the_middle_pair_when_even(hud):
    for secs in (0.40, 0.50):
        await hud.record(total_secs=secs)

    assert hud.median_ms() == 450.0


async def test_median_is_none_before_the_first_turn(hud):
    assert hud.median_ms() is None


async def test_appends_one_json_line_per_turn(hud, tmp_path):
    await hud.record(total_secs=0.4)
    await hud.record(total_secs=0.5)

    lines = (tmp_path / "metrics.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["end_of_speech_to_first_audio_ms"] == 500.0


async def test_an_unwritable_log_does_not_break_the_demo(sent, tmp_path):
    # The metrics log is a nicety; a failed write must never take down a turn.
    unwritable = tmp_path / "no-such-dir" / "metrics.jsonl"

    async def send(message):
        sent.append(message)

    hud = LatencyHUD(send, log_path=unwritable)
    await hud.record(total_secs=0.4)

    assert len(sent) == 1


async def test_without_a_filler_first_audio_and_first_content_agree(hud, sent):
    await hud.record(total_secs=1.6)

    payload = sent[0]["payload"]
    assert payload["end_of_speech_to_first_audio_ms"] == 1600.0
    assert payload["end_of_speech_to_first_content_ms"] == 1600.0
    assert payload["filler"] is None


async def test_a_filler_separates_first_sound_from_the_answer(hud, sent):
    # The point of the split: a cached filler makes the agent audible almost
    # immediately, but the answer is no earlier than it ever was. Reporting
    # only the first number would flatter us with something that does not mean
    # what it looks like.
    await hud.record(total_secs=0.42, content_secs=2.10, filler="weighing")

    payload = sent[0]["payload"]
    assert payload["end_of_speech_to_first_audio_ms"] == 420.0
    assert payload["end_of_speech_to_first_content_ms"] == 2100.0
    assert payload["filler"] == "weighing"


async def test_each_median_is_tracked_separately(hud):
    for audio, content in ((0.40, 2.0), (0.44, 2.2), (0.42, 2.1)):
        await hud.record(total_secs=audio, content_secs=content, filler="thinking")

    assert hud.median_ms() == 420.0
    assert hud.median_content_ms() == 2100.0
