# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regex pieces of the command rules of :mod:`dokimi.repos.security`, kept apart for the length of that file.

A rule's scan must be linear in the length of the command line: it starts at one place of each segment of the line, not at every keyword, so a line of repeated keywords is read once (#688, #1269).
"""

from __future__ import annotations

#: Blanks between the words of one command: a newline ends the command (``rm -rf build`` then ``cat /etc/x``).
BLANKS = r"[^\S\n]+"
SYSTEM_DIRS = "etc|usr|var|home|root|opt|bin|sbin|lib|lib64|boot|srv|dev|sys|proc"
#: A path under a system directory with one of these components is routine cleanup (apt lists, pip
#: and conda caches, an installed package), not the directory: ``/var/lib/apt/lists/*``, ``/root/.cache/pip``.
CLEANUP_PARTS = r"\.?cache|lists|pkgs|tmp|site-packages|dist-packages|__pycache__"
#: A pip option whose value is a URL that is no package (an index or a links page).
PIP_URL_OPTS = r"-i|-f|--index-url|--extra-index-url|--find-links"
#: ``pip install`` followed by a blank. unpinned-install starts at the first one after the start of its segment (a newline, `;`, `&`, `|`)
#: or after the option word `-i=` that stops the words of an earlier one (:data:`PIP_STUCK`): a later one reads the same words from there (#1269).
PIP_INSTALL = r"\bpip3?" + BLANKS + r"install\b(?=" + BLANKS + r")"
#: A package word that is a URL, has git+ in it, or is the system guard's override.
PIP_TARGET = r"(?:[^\s;&|]*git\+|[\"']?https?://|--break-system-packages\b)"
#: The words after ``install``, up to a target, read once and not given back: an index or links option takes its URL value as one word.
PIP_WORDS = (r"(?:" + BLANKS + r"(?!" + PIP_TARGET + r")(?:(?:" + PIP_URL_OPTS + r")(?:" + BLANKS + r"|=)[^\s;&|]+"
             r"|(?!(?:" + PIP_URL_OPTS + r")(?:[\s=;&|]|$))[^\s;&|]+))*+")
#: The option word `-i=` that no word can follow, where :data:`PIP_WORDS` ends. A `-i=` taken as the value of `-i` is no such word, so a line of `-i -i=` is read once (#1269).
PIP_STUCK = BLANKS + r"(?:" + PIP_URL_OPTS + r")=(?!\S)"
#: pipe-to-shell starts at the first download of a segment (up to `|`, `;` or `&`): a later one reaches the same `|` (#1269).
DOWNLOAD = r"\b(?:curl|wget)\b"

#: secret-read's start inside a word: a `/` of the word, then a later `/.ssh` and the like. The first `/` of the word reaches every `/.name` that a later `/` does,
#: so the scan starts at the word's start and no later: a word of repeated path parts is read once (#688).
SECRET_WORD_START = r"(?<!\S)[^\s/]*+/\S*?"

_RM_FLAG = r"(?:-[a-z]*r[a-z]*|--recursive)"
# the words after the recursive flag, up to a target: /, /*, ~, ~/, $HOME[/][*] or a system directory, bare or with a path under it that is no cache
_RM_TARGET = (
    r"(?:[^\s;&|]+" + BLANKS + r")*?[\"']?"
    r"(?:/\*?|(?:~|\$HOME|\$\{HOME(?:[:#%/^,?+=-][^}\s;&|\"'`()]*)?\})(?:/\.\.)*/?\*?|/(?:" + SYSTEM_DIRS + r")"
    r"(?!(?![^\s;&|\"']*\.\.)[^\s;&|\"']*/(?:" + CLEANUP_PARTS + r")(?:/|[\s;&|\"')`]|$))(?:/[^\s;&|\"']*)?)"
    r"[\"']?(?=[\s;&|)`]|$)")
# `rm`, flags none of which holds a `;`, `&` or `|`, and the first recursive flag
_RM_OPEN = r"\brm" + BLANKS + r"(?:-[^\s;&|]+" + BLANKS + r")*?" + _RM_FLAG + BLANKS
#: ``rm`` with a recursive flag, then a target among the words. A later `rm` of a segment finds its target only where the first `rm` that has a recursive flag does,
#: so the scan starts there and no later; a flag word that holds a `;`, `&` or `|` ends the segment inside the flags, which the second branch reads from each `rm` (#688).
RM_RECURSIVE = (
    r"(?:^|[;&|\n])(?:(?!" + _RM_OPEN + r")[^;&|\n])*(?>" + _RM_OPEN + r")" + _RM_TARGET
    + r"|\brm" + BLANKS + r"(?>(?:-[^\s;&|]+" + BLANKS + r")*)(?=-[^\s;&|]*[;&|])-\S+" + BLANKS
    + r"(?:-\S+" + BLANKS + r")*?" + _RM_FLAG + BLANKS + _RM_TARGET)
