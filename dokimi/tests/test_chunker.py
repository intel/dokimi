# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import pytest

from dokimi.ingest.chunker import chunk_text


def test_text_that_fits_one_chunk_is_one_chunk():
    text = "a" * 800
    assert chunk_text(text) == [text]


def test_no_chunk_is_a_repeat_of_the_previous_tail():
    text = "".join(chr(ord("a") + i % 26) for i in range(980))
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    # The 12th chunk ends exactly at the end; a 13th would be its last 20 characters.
    assert [len(c) for c in chunks] == [100] * 12
    assert "".join(c[:80] for c in chunks[:-1]) + chunks[-1] == text


def test_final_chunk_reaches_the_end_of_the_text():
    text = "x" * 950
    chunks = chunk_text(text, chunk_size=800, overlap=80)
    assert len(chunks) == 2
    assert chunks[-1] == text[720:]


def test_whitespace_is_normalised_and_empty_text_has_no_chunks():
    assert chunk_text("a \n\t b") == ["a b"]
    assert chunk_text("  \n ") == []


@pytest.mark.parametrize("size, overlap", [(0, 0), (10, 10), (10, -1)])
def test_bad_sizes_are_rejected(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("text", chunk_size=size, overlap=overlap)


def test_one_character_past_chunk_size_starts_a_second_chunk():
    chunks = chunk_text("a" * 801)
    assert [len(c) for c in chunks] == [800, 81]


@pytest.mark.parametrize("length", [99, 100, 101, 179, 180, 181, 259, 260, 261])
def test_chunks_reassemble_to_the_text_around_the_boundaries(length):
    text = "".join(chr(ord("a") + i % 26) for i in range(length))
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert "".join(c[:80] for c in chunks[:-1]) + chunks[-1] == text
