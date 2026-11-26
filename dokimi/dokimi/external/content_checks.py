# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Token-free content checks on the text of a skill, run by :func:`dokimi.external.validate.review_skills`.

- ``legacy-model-id`` (warn): the text names a model ID its vendor has deprecated or retired. The IDs,
  their status and the vendor's replacement are in ``legacy_models.toml`` beside this module; its header
  says which page each vendor's rows were checked against and on which date.
- ``hidden-unicode`` (warn): a bidi control, a Unicode tag character, or a stray zero-width character.
  Each is reported with its line and codepoint. Exempt: a zero-width joiner (U+200C, U+200D) next to an
  emoji (U+2600 to U+27BF, U+1F000 to U+1FAFF) or in Arabic and Indic script (U+0600 to U+0DFF), and a byte
  order mark at the start of a file. A line with more than :data:`FILLERS_PER_LINE` blank fillers that are
  exempt beside Greek or Hangul reports all of them.

Scanned: ``SKILL.md`` and the text files beside it (``.md``, ``.py``, ``.json``, ``.bash``, ``.html``, ``.csv`` and the other
suffixes of :data:`TEXT_SUFFIXES`) in the skill
directory. Left out: hidden folders, the vendored and dependency folders of
:data:`dokimi.skills.loader.VENDORED_DIR_NAMES` (``site-packages``, ``node_modules``, ``.venv`` ...), folders
that hold another skill's ``SKILL.md`` (that skill is checked on its own), symlinks, and files that are not
regular (a FIFO, a socket, a device). A skill whose
``SKILL.md`` sits at the checkout root is checked on that file only: its directory is the whole repository.
"""

from __future__ import annotations

import functools
import os
import re
import tomllib
import unicodedata
from pathlib import Path

from dokimi.skills import Skill
from dokimi.skills.loader import VENDORED_DIR_NAMES

from .lookalikes import readings

DATA_FILE = Path(__file__).with_name("legacy_models.toml")
TEXT_SUFFIXES = frozenset(
    {
        ".md", ".txt", ".rst", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
        ".bash", ".zsh", ".ps1", ".mjs", ".tsx", ".html", ".xml", ".csv", ".jsonl", ".mdx", ".cfg", ".ini",
    }
)
MAX_BYTES = 1 << 20  # a bundled file over a megabyte is data, not instructions the model reads
REPORT_PER_FILE = 5  # hidden characters named per file; the rest are counted
FILLERS_PER_LINE = 3  # exempt U+034F, U+115F or U+1160 fillers a line may hold; more than that carries hidden bits (#1274)

BIDI_CONTROLS = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
BIDI_MARKS = frozenset({0x200E, 0x200F, 0x061C})  # LRM, RLM, ALM: reported unless a right-to-left letter is beside them
TAG_CHARACTERS = frozenset(range(0xE0000, 0xE0080))
ZERO_WIDTH = frozenset({0x200B, 0x2060, 0xFEFF})  # a BOM is exempt at offset 0 (see _is_hidden)
SOFT_HYPHEN = 0x00AD
INVISIBLE_FORMAT = frozenset(range(0x2061, 0x2065)) | frozenset(range(0x206A, 0x2070))  # invisible operators, deprecated
VARIATION_SELECTORS = frozenset(range(0xFE00, 0xFE10)) | frozenset(range(0xE0100, 0xE01F0))
KEYCAP_BASES = frozenset("#*0123456789")  # an ASCII character a selector may follow (keycap sequences)
BLANK_FILLERS = frozenset({0x034F, 0x180E, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800})  # draw nothing
JOINERS = frozenset({0x200C, 0x200D})  # ZWNJ, ZWJ: exempt in emoji and Arabic or Indic text

Hit = tuple[str, str]  # (finding code, message)


@functools.lru_cache(maxsize=1)
def _legacy_index() -> tuple[re.Pattern, dict[str, dict], dict[str, str]]:
    """The pattern matching any listed ID as a whole token, the entry per ID, and the vendor source pages."""
    data = tomllib.loads(DATA_FILE.read_text(encoding="utf-8"))
    by_id: dict[str, dict] = {}
    for entry in data["model"]:
        for model_id in entry["ids"]:
            by_id[model_id] = entry
    # Longest first, so a listed ID that extends another (claude-sonnet-4-5-20250929) wins over its prefix.
    alternatives = "|".join(re.escape(m) for m in sorted(by_id, key=len, reverse=True))
    # Whole token, case-sensitive, ASCII neighbours only (CJK text has no spaces), not before a version step
    # ("gpt-4" in "gpt-4.1").
    pattern = re.compile(rf"(?<![A-Za-z0-9_.\-])(?:{alternatives})(?![A-Za-z0-9_\-]|\.[A-Za-z0-9])")
    return pattern, by_id, data["sources"]


def _legacy_message(where: str, line: int, model_id: str, entry: dict, sources: dict[str, str]) -> str:
    vendor, date, replacement = entry["vendor"], entry["date"], entry["replacement"]
    if entry["status"] == "retired":
        state = f"retired by {vendor}" + (f" on {date}" if date else "")
    else:
        state = f"deprecated by {vendor}" + (f", retires {date}" if date else "")
    advice = (
        f"use {replacement} (the vendor's replacement; check it is still served)"
        if replacement
        else f"the vendor names no replacement (see {sources[vendor]})"
    )
    return f"{where} line {line}: model ID {model_id!r} is {state}; {advice}"


def _legacy_hits(where: str, text: str) -> list[Hit]:
    """One finding per listed ID per file, on its first line."""
    pattern, by_id, sources = _legacy_index()
    hits: list[Hit] = []
    seen: set[str] = set()
    # Every reading has the length of the text, so the line is the line of the original.
    found = [m for reading in readings(text) for m in pattern.finditer(reading)]
    end = 0
    # First occurrence first, whichever reading found it; of matches at one place the longest, and none inside it.
    for match in sorted(found, key=lambda m: (m.start(), -m.end())):
        if match.start() < end:
            continue
        end = match.end()
        model_id = match.group()
        if model_id in seen:
            continue
        seen.add(model_id)
        line = text.count("\n", 0, match.start()) + 1
        message = _legacy_message(where, line, model_id, by_id[model_id], sources)
        hits.append(("legacy-model-id", message))
    return hits


def _joiner_exempt(text: str, index: int) -> bool:
    """A ZWJ or ZWNJ is expected when a neighbour is an emoji or symbol, or Arabic or Indic script."""
    for j in (index - 1, index + 1):
        if 0 <= j < len(text):
            cp = ord(text[j])
            if 0x2600 <= cp <= 0x27BF or 0x1F000 <= cp <= 0x1FAFF or 0x0600 <= cp <= 0x0DFF:
                return True
    return False


def _bidi_mark_exempt(text: str) -> set[int]:
    """Indexes of the LRM, RLM and ALM expected next to a right-to-left letter (Hebrew, Arabic), where it orders
    the text. Two linear passes record the class of the nearest letter on each side within the line, past
    neutrals, digits and other bidi marks. A character of class R or AL that is not a letter (a Hebrew
    punctuation mark such as U+05BE) is passed over like any other neutral."""
    marks = [i for i, ch in enumerate(text) if ord(ch) in BIDI_MARKS]
    if not marks:
        return set()
    exempt: set[int] = set()
    for order in (range(len(text)), range(len(text) - 1, -1, -1)):
        nearest = ""
        for i in order:
            ch = text[i]
            if ch == "\n":
                nearest = ""
            elif ord(ch) in BIDI_MARKS:
                if nearest in ("R", "AL"):
                    exempt.add(i)
            else:
                cls = unicodedata.bidirectional(ch)
                if cls in ("L", "R", "AL") and unicodedata.category(ch).startswith("L"):
                    nearest = cls
    return exempt


def _selector_stray(text: str, index: int) -> bool:
    """A variation selector at the start, after another selector, or after a base that takes none: ASCII that is not
    a keycap base, or a non-ASCII character that is not a symbol (``S*``) or an ideograph."""
    if index == 0 or ord(text[index - 1]) in VARIATION_SELECTORS:
        return True
    before = text[index - 1]
    if before.isascii():
        return before not in KEYCAP_BASES
    return not (unicodedata.category(before).startswith("S") or unicodedata.name(before, "").startswith("CJK"))


def _is_hangul(cp: int) -> bool:
    """Jamo, compatibility jamo, Extended-A jamo, syllables with Extended-B jamo, and halfwidth Hangul."""
    return 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F or 0xA960 <= cp <= 0xA97F or 0xAC00 <= cp <= 0xD7FF or 0xFFA0 <= cp <= 0xFFDC


def _filler_exempt(text: str, index: int) -> bool:
    """U+034F, U+115F, U+1160 and U+2800 are text in Greek or other non-ASCII script, Hangul and Braille."""
    cp = ord(text[index])
    near = [ord(text[j]) for j in (index - 1, index + 1) if 0 <= j < len(text) and ord(text[j]) not in BLANK_FILLERS]
    if cp == 0x2800:  # a run of blank patterns is Braille when a pattern borders it
        for step in (-1, 1):
            j = index + step
            while 0 <= j < len(text) and text[j] == "\u2800":
                j += step
            if 0 <= j < len(text):
                near.append(ord(text[j]))
    if cp in (0x115F, 0x1160):
        return any(_is_hangul(n) for n in near)
    if cp == 0x2800:
        return any(0x2800 <= n <= 0x28FF for n in near)
    if cp == 0x034F:
        return any(n > 0x7F and unicodedata.category(chr(n))[0] in "LM" for n in near)
    return False


def _is_hidden(text: str, index: int, bidi_exempt: set[int]) -> bool:
    cp = ord(text[index])
    if cp == SOFT_HYPHEN or cp in INVISIBLE_FORMAT:
        return True
    if cp in VARIATION_SELECTORS:
        return _selector_stray(text, index)
    if cp in BLANK_FILLERS:
        return not _filler_exempt(text, index)
    if cp in BIDI_MARKS:
        return index not in bidi_exempt
    if cp in BIDI_CONTROLS or cp in TAG_CHARACTERS:
        return True
    if cp == 0xFEFF:
        return index != 0  # a BOM at the start of a file is expected
    if cp in ZERO_WIDTH:
        return True
    if cp in JOINERS:
        return not _joiner_exempt(text, index)
    return False


def _hidden_hits(where: str, text: str) -> list[Hit]:
    bidi_exempt = _bidi_mark_exempt(text)
    found = [i for i in range(len(text)) if _is_hidden(text, i, bidi_exempt)]
    kept: dict[int, list[int]] = {}  # line start -> fillers exempt as script text (not Braille: a run of blanks is normal)
    for i, ch in enumerate(text):
        if ord(ch) in (0x034F, 0x115F, 0x1160) and _filler_exempt(text, i):
            kept.setdefault(text.rfind("\n", 0, i), []).append(i)
    found = sorted(found + [i for run in kept.values() if len(run) > FILLERS_PER_LINE for i in run])
    hits: list[Hit] = []
    for index in found[:REPORT_PER_FILE]:
        ch = text[index]
        line = text.count("\n", 0, index) + 1
        name = unicodedata.name(ch, "unnamed")
        hits.append(
            (
                "hidden-unicode",
                f"{where} line {line}: U+{ord(ch):04X} {name} can hide text from a reader; remove it",
            )
        )
    if len(found) > REPORT_PER_FILE:
        hits.append(
            ("hidden-unicode", f"{where}: {len(found) - REPORT_PER_FILE} more hidden characters not listed")
        )
    return hits


def _holds_skill(folder: Path) -> bool:
    """Whether ``folder`` holds another skill's ``SKILL.md``. A folder the walk cannot stat holds none."""
    try:
        return (folder / "SKILL.md").exists()
    except OSError:  # e.g. EACCES on a folder without search permission: the walk must not stop on it
        return False


def _text_files(skill_dir: Path) -> list[Path]:
    """The text files under ``skill_dir``, in walk order; hidden folders, symlinks, non-regular files and
    oversize files are left out."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):  # does not descend into symlinked folders
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in VENDORED_DIR_NAMES and not d.startswith(".") and not _holds_skill(here / d)
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                # A FIFO, socket or device is not a file to read: reading a FIFO blocks until a writer opens it.
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:  # lstat/stat on a listed file in a folder without search permission
                continue
            found.append(path)
    return found


def content_findings(skill: Skill, *, at_root: bool = False) -> list[Hit]:
    """``(code, message)`` for each content check that fails on ``skill``'s ``SKILL.md`` and bundled text files.

    ``at_root``: the ``SKILL.md`` sits at the checkout root, so only that file is read (see the module docstring).
    """
    skill_dir = skill.path.parent
    paths = [skill.path]
    if not at_root:
        paths += [p for p in _text_files(skill_dir) if p != skill.path]
    hits: list[Hit] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable or not UTF-8: a bundled file is skipped unread, SKILL.md is the loader's
        where = path.relative_to(skill_dir).as_posix()
        hits += _legacy_hits(where, text)
        hits += _hidden_hits(where, text)
    return hits
