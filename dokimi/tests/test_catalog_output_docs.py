# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The documented contract of ``catalog check`` output: docs/finding-codes.md and docs/catalog-jsonl.md.

External studies read these files, so each must list what the scan emits: every finding code the review
can report, and every key a skill row carries.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_catalog_ignore import _emitted_codes, _run, _skill

from dokimi.external.validate import FINDING_CODES

DOCS = Path(__file__).resolve().parent.parent / "docs"
ROW_TABLE = re.compile(r"^\| `([a-z0-9_-]+)` \|", re.MULTILINE)


def _table_keys(doc: str, heading: str) -> list[str]:
    """The first-column code spans of the table under ``## heading`` (up to the next ``## ``)."""
    section = doc.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    return ROW_TABLE.findall(section)


def test_every_finding_code_is_documented_once():
    doc = (DOCS / "finding-codes.md").read_text(encoding="utf-8")
    documented = _table_keys(doc, "Codes")

    assert len(documented) == len(set(documented)), "a code is listed twice"
    assert set(documented) == set(FINDING_CODES)
    # The codes the review code emits from source are a subset of the documented ones.
    assert _emitted_codes() <= set(documented)


def test_every_skill_row_key_and_finding_code_is_documented(tmp_path, capsys):
    # A scan with each kind of row: a clean skill, a warning, a name mismatch (an error), and a bad link.
    _skill(tmp_path / "skills", "clean", name="clean")
    _skill(tmp_path / "skills", "renamed", name="other-name")
    broken = tmp_path / "skills" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text(
        "---\nname: broken\ndescription: Use for broken things.\n---\nSee [notes](missing.md).\n", encoding="utf-8"
    )
    jsonl = tmp_path / "rows.jsonl"

    _run(["--root", str(tmp_path), "--kind", "skills", "--per-skill", "--jsonl", str(jsonl)], capsys)
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]

    documented_keys = set(_table_keys((DOCS / "catalog-jsonl.md").read_text(encoding="utf-8"), "Skill row"))
    assert {key for row in rows for key in row} == documented_keys
    assert {"link-missing", "name-mismatch"} <= {f["code"] for row in rows for f in row["findings"]}

    documented_codes = set(_table_keys((DOCS / "finding-codes.md").read_text(encoding="utf-8"), "Codes"))
    for row in rows:
        for finding in row["findings"]:
            assert finding["code"] in documented_codes
            assert set(finding) == {"level", "code", "message"}
            assert finding["level"] in {"note", "warn", "error"}


def test_whole_catalog_json_keys_are_documented(tmp_path, capsys):
    _skill(tmp_path / "skills", "clean", name="clean")

    out, _ = _run(["--root", str(tmp_path), "--kind", "skills", "--format", "json"], capsys)
    report = json.loads(out)

    doc = (DOCS / "catalog-jsonl.md").read_text(encoding="utf-8")
    top = doc.split("## `--format json`", 1)[1].split("## `--per-skill", 1)[0]
    documented_top = set(re.findall(r"^\| `([a-z0-9_]+)` \|", top, re.MULTILINE))
    assert set(report) <= documented_top | {"online"}
    assert {"root", "skills", "tasks", "granularity"} <= documented_top
