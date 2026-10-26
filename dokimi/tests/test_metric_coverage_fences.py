# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The static coverage metric reads structure outside code, and counts fences by markdown rules."""

from __future__ import annotations

from dokimi.metrics import coverage


def test_comment_lines_in_a_code_block_are_not_headings():
    doc = "```bash\n# install\n## build\n# run\n```\n"
    assert coverage._count_headings_and_blocks(doc) == (0, 1)


def test_real_headings_around_a_block_still_count():
    doc = "# Title\n\n```py\n# note\n```\n\n## Next\n"
    assert coverage._count_headings_and_blocks(doc) == (2, 1)


def test_tilde_fence_counts_as_a_block():
    doc = "~~~sh\n# comment\n~~~\n"
    assert coverage._count_headings_and_blocks(doc) == (0, 1)


def test_shorter_fence_inside_a_longer_one_does_not_close_it():
    doc = "````md\n```\n# inside\n```\n````\n# After\n"
    assert coverage._count_headings_and_blocks(doc) == (1, 1)


def test_fence_nested_in_a_list_item_counts_as_a_block():
    doc = "1. step\n\n    ```bash\n    # comment\n    ls\n    ```\n"
    assert coverage._count_headings_and_blocks(doc) == (0, 1)


def test_unclosed_fence_is_not_a_block_and_hides_later_hashes():
    doc = "# Top\n```bash\n# not a heading\n"
    assert coverage._count_headings_and_blocks(doc) == (1, 0)


def test_score_ignores_hash_comments_in_code():
    padded = "```bash\n" + "# step\n" * 6 + "```\n"
    real = "# a\n# b\n# c\n# d\n# e\n# f\n"
    assert coverage.score(padded) < coverage.score(real)


def test_closing_fence_must_use_the_same_character():
    doc = "```bash\n~~~\n# inside\n```\n# After\n"
    assert coverage._count_headings_and_blocks(doc) == (1, 1)


def test_text_after_a_closing_fence_keeps_the_block_open():
    doc = "```bash\n``` trailing\n# inside\n```\n# After\n"
    assert coverage._count_headings_and_blocks(doc) == (1, 1)


def test_backtick_in_the_info_string_is_not_a_fence():
    doc = "```bash`x\n# heading\n"
    assert coverage._count_headings_and_blocks(doc) == (1, 0)


def test_a_hash_run_is_a_heading_only_before_whitespace_or_the_end():
    assert coverage._count_headings_and_blocks("#hashtag\n#\n# Title\n") == (2, 0)
