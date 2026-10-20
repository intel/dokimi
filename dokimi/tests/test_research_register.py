# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/research_register.py and the records in research/records/."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "research_register", PROJECT / "scripts" / "research_register.py")
assert _SPEC and _SPEC.loader
rr = importlib.util.module_from_spec(_SPEC)
# dataclasses look the module up in sys.modules while the class body runs.
sys.modules[_SPEC.name] = rr
_SPEC.loader.exec_module(rr)


def _write(root: Path, kind: str, rid: str, front: str, body: str = "") -> None:
    path = root / "records" / kind / f"{rid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nid: {rid}\ntype: {kind}\ndate: 2026-10-14\n{front}---\n{body}\n")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copy(rr.SCHEMA, tmp_path / rr.SCHEMA.name)
    _write(tmp_path, "idea", "I001", "title: Compress\ntheme: O\nquestion: Q?\nstatus: open\n")
    _write(tmp_path, "experiment", "X001",
           "title: Run 1\nidea: I001\nhypothesis: H\noutcome: refuted\n",
           "<!--\nSPDX-License-Identifier: Apache-2.0\n-->\n## Result\nworse")
    _write(tmp_path, "experiment", "X002",
           "title: Run 2\nidea: I001\nhypothesis: H\noutcome: supported\n")
    _write(tmp_path, "finding", "F001", "title: F\nexperiments: [X001]\nconfidence: med\n")
    _write(tmp_path, "decision", "D001", "title: D\nfindings: [F001]\nstate: rejected\n")
    return tmp_path


def test_repository_register_is_valid() -> None:
    assert rr.main(["check"]) == 0


def test_valid_register_passes(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "check"]) == 0
    assert "OK: 5 records" in capsys.readouterr().out


@pytest.mark.parametrize(("kind", "rid", "front", "message"), [
    ("experiment", "X003", "title: T\nidea: I009\nhypothesis: H\noutcome: refuted\n",
     "no record I009"),
    ("experiment", "X003", "title: T\nidea: F001\nhypothesis: H\noutcome: refuted\n",
     "F001 is finding, want idea"),
    ("experiment", "X003", "title: T\nidea: I001\nhypothesis: H\noutcome: maybe\n",
     "'maybe' is not one of"),
    ("experiment", "X003", "title: T\nidea: I001\nhypothesis: H\noutcome: refuted\nextra: 1\n",
     "Unevaluated properties"),
    ("decision", "D002", "title: T\nstate: superseded\n", "names its superseded_by"),
    ("finding", "X009", "title: T\nexperiments: [X001]\nconfidence: low\n",
     "a finding id starts with F"),
    ("idea", "I002", "title: T\ntheme: O\nquestion: Q\nstatus: open\nlinks: [https://example.com/x]\n",
     "does not match"),
])
def test_bad_record_fails(root: Path, capsys: pytest.CaptureFixture[str],
                          kind: str, rid: str, front: str, message: str) -> None:
    _write(root, kind, rid, front)
    assert rr.main(["--root", str(root), "check"]) == 1
    assert message in capsys.readouterr().out


def test_duplicate_id_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / "records" / "idea" / "I001.md").rename(root / "records" / "idea" / "copy.md")
    _write(root, "idea", "I001", "title: Again\ntheme: O\nquestion: Q?\nstatus: open\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "duplicate id" in capsys.readouterr().out


def test_query_negative_outcomes_for_idea(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "query", "--type", "experiment", "--ref", "I001",
                    "--where", "outcome=refuted,inconclusive"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[0] for line in out] == ["X001"]


def test_next_id(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "next", "experiment"]) == 0
    assert capsys.readouterr().out.strip() == "X003"


def test_export_tree(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "export"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"from": "X001", "to": "I001", "rel": "idea"} in data["edges"]
    theme = next(t for t in data["themes"] if t["id"] == "O")
    assert theme["ideas"][0]["experiments"] == [
        {"id": "X001", "outcome": "refuted"}, {"id": "X002", "outcome": "supported"}]
    assert next(r for r in data["records"] if r["id"] == "X001")["body"] == "## Result\nworse"
