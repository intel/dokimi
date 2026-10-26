# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Readability scoring helpers."""

from __future__ import annotations

import re

from dokimi.metrics.coverage import fenced_lines


# Latin + Cyrillic are intentional here for multilingual readability scoring (RUF001-safe).
# A stop ends a sentence only before whitespace or the end, so "docs.example.com" and "2.5.1" do not.
SENTENCE_RE = re.compile(r"[.!?]+(?=[\"'\u201d\u2019)\]*_]*(?:\s|$))")
WORD_RE = re.compile(r"[A-Za-zА-Яа-я0-9'-]+")
VOWELS = "aeiouyаеёиоуыэюяAEIOUYАЕЁИОУЫЭЮЯ"
VOWEL_GROUP_RE = re.compile(rf"[{VOWELS}]+")
INLINE_CODE_RE = re.compile(r"`[^`]+`")


def _syllables(word: str) -> int:
    """Approximate syllables by counting contiguous vowel groups."""
    count = len(VOWEL_GROUP_RE.findall(word))
    return max(1, count)


def _prose(text: str) -> str:
    """Return the text with fenced block lines removed, using the coverage metric's fence rules."""
    lines = re.split(r"\r\n|\r|\n", text)
    inside, _ = fenced_lines(lines)
    return "\n".join(line for line, code in zip(lines, inside, strict=True) if not code)


def score(text: str, grade_max: float = 12.0) -> float:
    """Return inverse FK grade score in [0, 1] where higher is better."""
    if grade_max <= 0:
        raise ValueError("grade_max must be > 0")

    cleaned = INLINE_CODE_RE.sub(" ", _prose(text))

    words = WORD_RE.findall(cleaned)
    n_words = len(words)
    if n_words == 0:
        return 0.0
    n_sentences = max(1, len(SENTENCE_RE.findall(cleaned)))
    n_syllables = sum(_syllables(w) for w in words)

    grade = 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59
    if grade <= 0:
        return 1.0
    if grade >= grade_max:
        return 0.0
    return round(1.0 - (grade / grade_max), 4)
