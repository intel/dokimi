# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The replay patch's subject takes a pull request title with a surrogate (#885)."""

from __future__ import annotations

from types import SimpleNamespace

from dokimi.repos.replay.build import _mbox


def _mail(title: str) -> bytes:
    return _mbox(SimpleNamespace(title=title, number=1), "abc", b"diff")  # type: ignore[arg-type]


def test_a_lone_surrogate_in_the_title_is_written_as_a_question_mark():
    """#885: ``_mbox`` encoded the header strictly and raised UnicodeEncodeError."""
    assert b"Subject: [PATCH] n?\n" in _mail("n" + chr(0xD800))


def test_a_surrogate_pair_in_the_title_is_its_utf8_character():
    """#885: a JSON escape pair is one character in the subject."""
    assert "Subject: [PATCH] a😀\n".encode() in _mail("a" + chr(0xD83D) + chr(0xDE00))


def test_a_plain_title_gives_the_same_mail():
    """#885: a title without a surrogate is unchanged, spaces folded and bytes after the header kept."""
    mail = _mail("  fix é\n  now ")
    assert mail == (b"From abc Mon Sep 17 00:00:00 2001\nFrom: Reviewed Change <review@localhost>\n"
                    b"Date: Thu, 1 Jan 1970 00:00:00 +0000\nMIME-Version: 1.0\n"
                    b"Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n"
                    b"Subject: [PATCH] fix \xc3\xa9 now\n\n---\ndiff")
