"""Session telemetry — the record that explains a demo failure afterwards."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from loguru import logger

from app.telemetry import SessionTelemetry, _redact, environment_snapshot


@dataclass
class FakeSettings:
    gradium_api_key: str = "sk-live-do-not-log-me"
    general_compute_api_key: str = "gc-live-secret"
    jev_api_key: str = ""
    llm_model: str = "gpt-oss-120b"
    jev_timeout_s: float = 1.2
    enable_speaker_id: bool = True


@pytest.fixture
def tel(tmp_path):
    t = SessionTelemetry("test-session", tmp_path, settings=FakeSettings())
    yield t
    t.close()


def records(tel) -> list[dict]:
    return [json.loads(line) for line in tel.path.read_text().splitlines() if line.strip()]


def test_a_session_opens_with_enough_context_to_interpret_it(tel):
    start = records(tel)[0]

    assert start["type"] == "session_start"
    # Without these a log is unreadable a day later.
    assert start["llm_model"] == "gpt-oss-120b"
    assert start["jev_timeout_s"] == 1.2
    assert "git_sha" in start and "pipecat" in start


def test_keys_are_recorded_as_presence_and_never_as_values(tel):
    start = records(tel)[0]
    blob = tel.path.read_text()

    assert start["has_gradium_key"] is True
    assert start["has_general_compute_key"] is True
    assert start["has_jev_key"] is False  # absent, so fallbacks explain themselves
    # The actual secrets must appear nowhere in the file.
    assert "sk-live-do-not-log-me" not in blob
    assert "gc-live-secret" not in blob


def test_anything_key_shaped_in_a_payload_is_redacted(tel):
    tel.write("suspicious", api_key="leak", nested={"auth_token": "leak2", "safe": 1})
    blob = tel.path.read_text()

    assert "leak" not in blob
    assert "leak2" not in blob
    assert '"safe": 1' in blob


def test_redaction_survives_lists_and_truncates_runaway_strings():
    out = _redact({"items": [{"password": "x"}], "essay": "y" * 5000})

    assert out["items"][0]["password"] == "<redacted>"
    assert len(out["essay"]) < 2200 and out["essay"].endswith("chars>")


async def test_an_utterance_is_filed_under_its_own_turn_not_the_previous_one(tel):
    # The user's words and the Jev decision about them both arrive *before* the
    # turn is accepted. Counting on acceptance filed each utterance under the
    # previous turn, which is exactly backwards when reading a log.
    publish = tel.publisher()

    await publish({"type": "transcript", "text": "explain attention"})
    await publish({"type": "jev", "kind": "end_of_turn"})
    await publish({"type": "interaction", "event": "turn_accepted"})
    await publish({"type": "transcript", "text": "wait, go back"})
    await publish({"type": "jev", "kind": "overlap"})
    await publish({"type": "interaction", "event": "turn_accepted"})

    rows = records(tel)
    turns = {r.get("text"): r["turn"] for r in rows if r["type"] == "transcript"}
    assert turns == {"explain attention": 1, "wait, go back": 2}
    # The decision about an utterance belongs to the same turn as the utterance.
    assert [r["turn"] for r in rows if r["type"] == "jev"] == [1, 2]


async def test_several_transcripts_in_one_utterance_stay_on_one_turn(tel):
    publish = tel.publisher()

    for text in ("so", "so how", "so how does that work"):
        await publish({"type": "transcript", "text": text})
    await publish({"type": "interaction", "event": "turn_accepted"})

    rows = [r for r in records(tel) if r["type"] == "transcript"]
    assert {r["turn"] for r in rows} == {1}


async def test_high_frequency_state_snapshots_are_not_logged(tel):
    publish = tel.publisher()

    for _ in range(50):
        await publish({"type": "state", "state": {"phase": "listening"}})

    assert not [r for r in records(tel) if r["type"] == "state"]


async def test_latency_samples_land_beside_the_events_that_caused_them(tel):
    await tel.metrics_publisher()(
        {"type": "metrics", "payload": {"end_of_speech_to_first_audio_ms": 412.0}}
    )

    metrics = [r for r in records(tel) if r["type"] == "metrics"]
    assert metrics[0]["end_of_speech_to_first_audio_ms"] == 412.0


def test_warnings_and_errors_are_captured_not_left_on_stdout(tel):
    logger.warning("gradium websocket closed unexpectedly")
    logger.error("jev timed out")

    logs = [r for r in records(tel) if r["type"] == "log"]
    assert {r["level"] for r in logs} == {"WARNING", "ERROR"}
    assert any("gradium websocket" in r["message"] for r in logs)


def test_browser_side_trouble_reaches_the_same_file(tel):
    tel.client_log({"level": "warn", "message": "autoplay blocked: NotAllowedError"})

    client = [r for r in records(tel) if r["type"] == "client"]
    assert "autoplay blocked" in client[0]["message"]


def test_closing_summarises_the_session(tmp_path):
    t = SessionTelemetry("closing", tmp_path, settings=FakeSettings())
    t.write("jev", kind="overlap")
    t.write("jev", kind="end_of_turn")
    t.close()

    end = json.loads(t.path.read_text().splitlines()[-1])
    assert end["type"] == "session_end"
    assert end["counts"]["jev"] == 2


def test_an_unwritable_path_degrades_instead_of_killing_the_session(tmp_path):
    # Telemetry must never be the reason a demo fails.
    t = SessionTelemetry("broken", tmp_path / "nope" / "deeper" / "\0bad", settings=None)
    t.write("anything", value=1)
    t.close()


def test_snapshot_tolerates_settings_missing_fields():
    snap = environment_snapshot(object())

    assert snap["has_gradium_key"] is False
    assert "git_sha" in snap
