# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Token-free content checks on the text of a skill, run by :func:`dokimi.external.validate.review_skills`.

- ``legacy-model-id`` (warn): the text names a model ID its vendor has deprecated or retired. The IDs,
  their status and the vendor's replacement are in ``legacy_models.toml`` beside this module; its header
  says which page each vendor's rows were checked against and on which date.
- ``hidden-unicode`` (warn): a bidi control, a Unicode tag character, or a stray zero-width character.
  Each is reported with its line and codepoint. Exempt: a zero-width joiner (U+200C, U+200D) next to an
  emoji (U+2600 to U+27BF, U+1F000 and up) or in Arabic and Indic script (U+0600 to U+0DFF), and a byte
  order mark at the start of a file.

Scanned: ``SKILL.md`` and the text files beside it (``.md``, ``.py``, ``.json`` and similar) in the skill
directory. Left out: hidden folders, the vendored and dependency folders of
:data:`dokimi.skills.loader.VENDORED_DIR_NAMES` (``site-packages``, ``node_modules``, ``.venv`` ...), folders
that hold another skill's ``SKILL.md`` (that skill is checked on its own), and symlinks. A skill whose
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

DATA_FILE = Path(__file__).with_name("legacy_models.toml")
TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".rst", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml", ".toml"}
)
MAX_BYTES = 1 << 20  # a bundled file over a megabyte is data, not instructions the model reads
REPORT_PER_FILE = 5  # hidden characters named per file; the rest are counted

BIDI_CONTROLS = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
TAG_CHARACTERS = frozenset(range(0xE0000, 0xE0080))
ZERO_WIDTH = frozenset({0x200B, 0x2060, 0xFEFF})  # a BOM is exempt at offset 0 (see _is_hidden)
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
    # Whole token, case-sensitive: not inside a longer name (a letter, digit, "-" or "_" around it), and not
    # before a version step ("gpt-4" in "gpt-4.1").
    pattern = re.compile(rf"(?<![\w.\-])(?:{alternatives})(?![\w\-]|\.\w)")
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
    for match in pattern.finditer(text):
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
            if 0x2600 <= cp <= 0x27BF or cp >= 0x1F000 or 0x0600 <= cp <= 0x0DFF:
                return True
    return False


def _is_hidden(text: str, index: int) -> bool:
    cp = ord(text[index])
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
    found = [i for i in range(len(text)) if _is_hidden(text, i)]
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


def _text_files(skill_dir: Path) -> list[Path]:
    """The text files under ``skill_dir``, in walk order; hidden folders, symlinks and oversize files are left out."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):  # does not descend into symlinked folders
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in VENDORED_DIR_NAMES and not d.startswith(".") and not (here / d / "SKILL.md").exists()
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES or path.is_symlink():
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
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
            continue  # unreadable: the loader or the scan reports it
        where = path.relative_to(skill_dir).as_posix()
        hits += _legacy_hits(where, text)
        hits += _hidden_hits(where, text)
    return hits
