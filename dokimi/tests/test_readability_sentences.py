# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Readability counts sentence ends, not the dots inside hostnames and numbers."""

from __future__ import annotations

import pytest

from dokimi.metrics import readability

PLAIN = "Open the page at {target} and read the guide to set up the tool for your team and then run the first check on the main file."


@pytest.mark.parametrize(
    ("dotted", "spaced"),
    [
        ("docs.example.com", "docs example com"),
        ("version 2.5.1", "version 2 5 1"),
        ("config.yaml", "config yaml"),
    ],
)
def test_dots_inside_a_token_do_not_end_a_sentence(dotted: str, spaced: str) -> None:
    assert readability.score(PLAIN.format(target=dotted)) == readability.score(PLAIN.format(target=spaced))


def test_real_sentence_ends_still_split() -> None:
    one = readability.score("Read the guide and then run the first check on the main file for your team today.")
    three = readability.score("Read the guide. Then run the first check! Is the main file ready?")
    assert three > one


def test_closing_quote_or_paren_after_a_stop_still_ends_the_sentence() -> None:
    quoted = readability.score('He said "stop." Then we left (quickly.) Nobody spoke.')
    plain = readability.score("He said stop. Then we left quickly. Nobody spoke.")
    assert quoted == plain
