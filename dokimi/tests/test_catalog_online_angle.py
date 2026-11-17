# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --online``: where a ``<...>`` link destination closes, and which of its text is a template."""

from __future__ import annotations

from dokimi.external import online_checks


def test_an_angle_destination_closes_only_at_a_bracket_a_link_ends_at():
    # A ">" in prose after a malformed "](<url" does not close the destination: the bare URL is cut as before.
    assert online_checks.extract_urls("[a](<https://acme.dev/a) is 3 > 2 today") == ["https://acme.dev/a"]
    assert online_checks.extract_urls("[a](<https://acme.dev/a b) is 3 > 2") == ["https://acme.dev/a"]
    # A ")", a title or the end of a definition's line after the ">" closes it.
    assert online_checks.extract_urls('[a](<https://acme.dev/a b> "t")') == ["https://acme.dev/a b"]
    assert online_checks.extract_urls("[a](<https://acme.dev/a b> )") == ["https://acme.dev/a b"]
    assert online_checks.extract_urls("[a]: <https://acme.dev/a b>  \nnext") == ["https://acme.dev/a b"]
    assert online_checks.extract_urls("[a]: <https://acme.dev/a b> trailing") == ["https://acme.dev/a"]


def test_a_template_marker_after_a_blank_in_an_angle_destination_is_text():
    assert online_checks.extract_urls("[a](<https://acme.dev/a b $5>)") == ["https://acme.dev/a b $5"]
    assert online_checks.extract_urls("[a](<https://acme.dev/a${V} b>)") == []
    assert online_checks.extract_urls("[a](<https://acme.dev/a{id}>)") == []
