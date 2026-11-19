# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fold look-alike letters to Latin before a command or model-ID scan reads the text (#960).

``fold`` keeps the length of the text, so an offset or a line number found in the folded text is the same in
the original. The table is a short hand-picked list of Cyrillic and Greek letters that read as a Latin letter,
not the full Unicode confusables list: a letter outside it still escapes.
"""

from __future__ import annotations

import unicodedata

_PAIRS = (
    # Cyrillic
    "а=a с=c е=e о=o р=p х=x у=y і=i ѕ=s һ=h ј=j ԁ=d ԛ=q ԝ=w "
    "А=A В=B Е=E К=K М=M Н=H О=O Р=P С=C Т=T Х=X І=I Ѕ=S Ј=J "
    # Greek
    "ο=o ν=v ι=i ρ=p α=a υ=u κ=k "
    "Α=A Β=B Ε=E Ζ=Z Η=H Ι=I Κ=K Μ=M Ν=N Ο=O Ρ=P Τ=T Υ=Y Χ=X"
)
CONFUSABLES = {ord(pair[0]): pair[2] for pair in _PAIRS.split()}


def _fold_char(ch: str, nfkc: str) -> str:
    if ch.isascii():
        return ch
    if ord(ch) in CONFUSABLES:
        return CONFUSABLES[ord(ch)]
    if not nfkc:
        return ch
    normal = unicodedata.normalize("NFKC", ch)
    if len(normal) != 1:
        return ch  # a character that expands would move every later offset
    if nfkc == "all":
        return normal
    # "letters": a letter or digit (not a superscript, which is a footnote mark) or a dot, hyphen or underscore
    word = (normal.isalnum() and unicodedata.category(ch) != "No") or normal in ".-_"
    return normal if word else ch


def fold(text: str, *, nfkc: str = "all") -> str:
    """``text`` with each look-alike letter, and each compatibility form (fullwidth, say) that is one character, as Latin.

    ``nfkc`` is ``"all"`` (every such form), ``"letters"`` (only a form that becomes a letter, a digit, ``.``, ``-`` or ``_``, so a
    fullwidth ``;`` and a superscript ``2`` stay) or ``""`` (the look-alike table alone).
    """
    return "".join(_fold_char(ch, nfkc) for ch in text)


def readings(text: str) -> list[str]:
    """The texts a scan reads, all of the length of ``text``: the raw text, the letters folded, and everything folded.

    Folding a character that is not a letter (a fullwidth ``;``, a superscript ``2``) can end or extend a match the
    raw text has, so each reading is scanned and the findings are joined.
    """
    found = [text]
    for reading in (fold(text, nfkc="letters"), fold(text)):
        if reading not in found:
            found.append(reading)
    return found
