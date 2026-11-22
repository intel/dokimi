# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which code spans of a README or guidance file name an identifier (``std::vector``, ``MKL_THREADS``, ``snake_case``, ``CamelCase``)."""

from __future__ import annotations

import re

_SHAPES = re.compile(r"^[A-Za-z_]\w*(::\w+)+$|^[A-Z][A-Z0-9_]{3,}$|^[a-z]+_[a-z_]+$")
_WORD = re.compile(r"\w+")
_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")


def _is_camel(span: str) -> bool:
    """``^[A-Z]\\w*[a-z]\\w*[A-Z]\\w*$``: a word that starts with a capital and has a lowercase letter before a capital.

    The first lowercase letter after the first character is the earliest one that can be followed by a
    capital, so two searches replace three overlapping runs (https://github.com/intel/dokimi/issues/999).
    """
    word = span[:-1] if span.endswith("\n") else span  # `$` also matches before a final line end
    if not _UPPER.match(word) or not _WORD.fullmatch(word):
        return False
    lower = _LOWER.search(word, 1)
    return lower is not None and _UPPER.search(word, lower.end()) is not None


def is_identifier(span: str) -> bool:
    """``span`` is ``a::b``, ``SCREAMING_CASE``, ``snake_case`` or ``CamelCase``; any word character may fill a name."""
    return bool(_SHAPES.match(span)) or _is_camel(span)
