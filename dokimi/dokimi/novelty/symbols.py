# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Known identifiers of a C library's API, from its headers and its exported symbols.

``skill novelty ground --header`` and ``--library`` pin the names a C API declares, so an answer
that names ``cblas_dgemmx`` (not in the library) is vetoed the way an invented ``--option`` is.
Only names with a prefix from ``--prefix`` are read, so an ordinary word is never checked.
"""

from __future__ import annotations

import glob
import hashlib
import re
from pathlib import Path
from typing import Sequence

from dokimi.process import Env, run
from dokimi.utils import InputError

#: A C identifier prefix: a C identifier that ends in an underscore (``cblas_``, ``LAPACKE_``).
_PREFIX = re.compile(r"[A-Za-z_]\w*_")
#: Comments and string or character literals of C code: no declaration is named in them.
_C_SKIP = re.compile(r'/\*.*?\*/|//(?:\\\n|[^\n])*|"(?:\\.|[^"\\\n])*"|\'(?:\\(?:x[0-9A-Fa-f]+|[0-7]{1,3}|.)|[^\'\\\n])\'', re.DOTALL)
_CHUNK = 1 << 20


def check_prefixes(prefixes: Sequence[str]) -> tuple[str, ...]:
    """The prefixes, sorted and without repeats; refused unless each is a C identifier prefix.

    Raises:
        InputError: A prefix is not a C identifier ending in ``_`` (``mkl`` is refused: it
            would also match ordinary words).
    """
    if not prefixes:  # an empty alternation matches every word of the answer
        raise InputError("give at least one --prefix: it says which names are checked")
    bad = [p for p in prefixes if not _PREFIX.fullmatch(p)]
    if bad:
        raise InputError(f"--prefix {bad[0]!r} is not a C identifier prefix ending in '_' "
                         "(for example cblas_)")
    return tuple(sorted(set(prefixes)))


_MEMBER_GAP = 8  # the blanks a member access may have between its `->` and the name


def symbol_pattern(prefixes: Sequence[str]) -> str:
    """A regex for the names with one of ``prefixes`` as C identifiers, not as parts of a word.

    A name after ``.`` or after ``->`` (with up to 8 blanks) is a member, and ``mkl_types`` in ``mkl_types.h`` is a file name:
    neither is an API name. A name after ``/`` is one only when it is called (``a/cblas_x(``), and
    a name after ``-`` is one when it is called or follows an operand (``f(x)-cblas_x``, ``x -cblas_x``).
    """
    alternatives = "|".join(re.escape(p) for p in prefixes)
    # `->` may be followed by up to _MEMBER_GAP blanks (not newlines): one fixed-width lookbehind each.
    # a name after `. ` stays an API name: a sentence's period and a space look the same.
    member = "".join(rf"(?<!->[ \t]{{{n}}})" for n in range(_MEMBER_GAP + 1))
    return rf"(?<![\w.]){member}(?:(?<!/)(?<![^\w)\]\s]-)(?<!^-)|(?=(?:{alternatives})\w+\s*\())(?:{alternatives})\w+(?!\.?\w)"


def header_names(text: str, prefixes: Sequence[str]) -> set[str]:
    """The names with a prefix that a C header's code mentions: macros, types, declarations.

    Comments and literals are skipped. A name a macro expands to counts too, so the set is a
    superset of the declared API: the check then misses an invented name only if the header
    itself spells it.
    """
    code = _C_SKIP.sub(" ", text)
    return set(re.findall(symbol_pattern(prefixes), code))


def nm_names(output: str, prefixes: Sequence[str]) -> set[str]:
    """The names with a prefix in ``nm -D --defined-only`` output, without a ``@@VER`` tag."""
    names: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3:  # address, type, name: an undefined name has no address
            continue
        name = fields[2].split("@", 1)[0]
        if re.fullmatch(r"\w+", name) and name.startswith(tuple(prefixes)):
            names.add(name)
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(patterns: Sequence[str], flag: str) -> list[Path]:
    """The files the ``--header`` or ``--library`` patterns name: globs expanded, sorted.

    Raises:
        InputError: A pattern names no file.
    """
    found: set[Path] = set()
    for pattern in patterns:
        # A path that exists is read as it is: glob would take ``[1]`` in ``inc[1]/`` for a class.
        if Path(pattern).is_file():
            matches = [Path(pattern)]
        else:
            matches = [Path(m) for m in glob.glob(pattern, recursive=True)]
        files = [m for m in matches if m.is_file()]
        if not files:
            raise InputError(f"--{flag} {pattern!r} matches no file")
        found.update(files)
    return sorted(found)


def _header_text(path: Path) -> str:
    """The text of a header; an unreadable one is an input error, not a traceback."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InputError(f"cannot read the header {path}: {exc.strerror or exc}") from None


def _library_output(path: Path, *, timeout: float = 120.0) -> str:
    """``nm -D --defined-only`` of ``path``, the whole output (a large library lists thousands)."""
    try:
        done = run(["nm", "-D", "--defined-only", "--", str(path)], timeout=timeout, env=Env.inherit(),
                   capture_limit=None)
    except FileNotFoundError:
        raise InputError(f"nm is not installed; cannot read the symbols of {path}") from None
    except OSError as exc:
        raise InputError(f"nm cannot be run on {path}: {exc.strerror or exc}") from None
    if done.timed_out or done.returncode != 0:
        lines = done.stderr.strip().splitlines()
        reason = lines[-1] if lines else ("timed out" if done.timed_out else f"exit {done.returncode}")
        raise InputError(f"nm cannot read {path}: {reason}")
    return done.stdout


def symbols_check(prefixes: Sequence[str], headers: Sequence[str],
                  libraries: Sequence[str]) -> dict:
    """An ``identifiers`` veto over the prefixed names of ``headers`` and ``libraries``.

    The check's ``within`` and ``pattern`` are :func:`symbol_pattern`, so each name the answer
    mentions with a prefix is one scope: a denial or a negation before it makes no claim, as
    for a tool's options. ``known`` is the union of every input's names; each input is recorded
    with the sha256 of its bytes, as a tool's ``--help`` is.

    Raises:
        InputError: A prefix is not valid, a pattern names no file, nm cannot read a library,
            or no input has a name with a prefix (a wrong header would veto every name).
    """
    prefixes = check_prefixes(prefixes)
    known: set[str] = set()
    inputs: list[dict] = []
    sources = [("header", _files(headers, "header")), ("library", _files(libraries, "library"))]
    for kind, paths in sources:
        for path in paths:
            if kind == "header":
                names = header_names(_header_text(path), prefixes)
            else:
                names = nm_names(_library_output(path), prefixes)
            known |= names
            inputs.append({"kind": kind, "path": str(path), "sha256": _sha256(path)})
    if not known:  # a wrong header would otherwise veto every name the answer mentions
        raise InputError(f"no name with the prefix {', '.join(prefixes)} in the given files")
    pattern = symbol_pattern(prefixes)
    return {
        "type": "identifiers", "tier": "verifiable", "role": "veto",
        "pattern": pattern, "within": pattern,
        "known": sorted(known),
        "source": {"kind": "symbols", "prefixes": list(prefixes), "inputs": inputs},
    }
