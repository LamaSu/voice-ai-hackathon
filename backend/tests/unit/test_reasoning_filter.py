"""The agent must never read its own chain-of-thought aloud."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.llm_general_compute import ReasoningFilter, looks_like_reasoning


@pytest.mark.parametrize(
    "text,expected",
    [
        ("… We need to respond appropriately. The user says", True),
        ("We should respond in a friendly short spoken style", True),
        ('The user says "Bro, that\'s brutal." Probably reacting', True),
        ("Note: we have multiple participants", True),
        ("As Jev, a voice assistant, replies are spoken", True),
        # ordinary speech that merely contains similar words later on
        ("Sure! We need to leave by six if you want to catch it.", False),
        ("Tokyo is the capital of Japan.", False),
        ("I get it — that sounded harsh. Anything else?", False),
        ("We can do that on Saturday.", False),
    ],
)
def test_looks_like_reasoning(text, expected):
    assert looks_like_reasoning(text) is expected


def spoken(frames) -> str:
    return "".join(f.text for f in frames if isinstance(f, LLMTextFrame))


async def test_reasoning_response_is_swallowed():
    down, _ = await run_test(
        ReasoningFilter(),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("… We need to "),
            LLMTextFrame("respond appropriately. The user says "),
            LLMTextFrame('"Bro, that\'s brutal."'),
            LLMFullResponseEndFrame(),
            SleepFrame(0.1),
        ],
    )
    assert spoken(down) == "", "the agent spoke its own notes"


async def test_normal_answer_passes_through_whole():
    down, _ = await run_test(
        ReasoningFilter(),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("Tokyo is "),
            LLMTextFrame("the capital of Japan, "),
            LLMTextFrame("and it's lovely in spring."),
            LLMFullResponseEndFrame(),
            SleepFrame(0.1),
        ],
    )
    assert spoken(down) == "Tokyo is the capital of Japan, and it's lovely in spring."


async def test_short_answer_ending_inside_the_sniff_window_is_still_spoken():
    down, _ = await run_test(
        ReasoningFilter(),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("Sure."),
            LLMFullResponseEndFrame(),
            SleepFrame(0.1),
        ],
    )
    assert spoken(down) == "Sure."


async def test_the_next_response_is_unaffected_by_a_suppressed_one():
    suppressed: list[str] = []

    async def on_suppressed(text):
        suppressed.append(text)

    down, _ = await run_test(
        ReasoningFilter(on_suppressed=on_suppressed),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("We need to respond appropriately here."),
            LLMFullResponseEndFrame(),
            SleepFrame(0.1),
            LLMFullResponseStartFrame(),
            LLMTextFrame("It's sunny tomorrow, around 20 degrees."),
            LLMFullResponseEndFrame(),
            SleepFrame(0.1),
        ],
    )
    assert spoken(down) == "It's sunny tomorrow, around 20 degrees."
    assert len(suppressed) == 1
