"""The model's thinking must never reach TTS."""

from __future__ import annotations

import pytest

from app.reasoning_filter import ReasoningFilter


@pytest.fixture
def f():
    return ReasoningFilter()


async def feed(filt: ReasoningFilter, *chunks: str) -> str:
    return "".join([await filt.filter(c) for c in chunks])


async def test_plain_text_passes_through_untouched(f):
    assert await feed(f, "Attention ", "weighs ", "each token.") == "Attention weighs each token."


async def test_a_reasoning_block_is_removed(f):
    out = await feed(f, "<think>The user wants a simple answer.</think>It weighs each token.")

    assert out == "It weighs each token."


async def test_a_tag_split_across_chunks_is_still_caught(f):
    # The real failure mode: streaming splits "<think>" and a per-chunk regex
    # leaks exactly the text it was meant to remove.
    out = await feed(f, "Sure. <thi", "nk>they want ", "brevity</thi", "nk>It weighs tokens.")

    assert out == "Sure. It weighs tokens."
    assert "they want" not in out and "brevity" not in out


async def test_a_partial_tag_is_not_spoken_early(f):
    # After this chunk nothing may have been emitted for "<thi" — it might
    # become a tag.
    first = await f.filter("Hello <thi")

    assert first == "Hello "


async def test_a_partial_tag_that_turns_out_to_be_ordinary_text_is_released(f):
    out = await feed(f, "Compare a < b", " and c < d.")

    assert out == "Compare a < b and c < d."


async def test_unterminated_reasoning_stays_silent_rather_than_narrating(f):
    # A model that opens a block and never closes it should make us quiet, not
    # make us read its notes aloud.
    out = await feed(f, "<think>I should explain that", " the mechanism is", " attention")

    assert out == ""


async def test_an_interruption_clears_state_so_the_next_turn_speaks(f):
    await feed(f, "<think>half a thought")
    await f.handle_interruption()

    assert await feed(f, "A fresh answer.") == "A fresh answer."


async def test_several_blocks_in_one_response(f):
    out = await feed(f, "<think>a</think>One. <think>b</think>Two.")

    assert out == "One. Two."


async def test_harmony_channel_markers_are_handled(f):
    out = await feed(f, "<|channel|>analysis<|message|>weighing options<|end|>The answer is four.")

    assert out == "The answer is four."


async def test_text_before_during_and_after(f):
    out = await feed(f, "Well, ", "<think>", "hmm", "</think>", "it depends.")

    assert out == "Well, it depends."


async def test_alternative_thinking_tag(f):
    assert await feed(f, "<thinking>x</thinking>Done.") == "Done."
