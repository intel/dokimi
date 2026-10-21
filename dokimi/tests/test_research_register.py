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
    ("experiment", "X003", "title: T\nidea: [[I001]]\nhypothesis: H\noutcome: refuted\n",
     "is not of type 'string'"),
    ("decision", "D002", "title: T\nstate: accepted\nsuperseded_by: D001\n",
     "superseded_by is set but state is 'accepted'"),
    ("decision", "D002", "title: T\nstate: superseded\nsuperseded_by: D002\n",
     "names the record itself"),
    ("idea", "I002", "title: T\ntheme: O\nquestion: Q\nstatus: open\nextra: 1\n",
     "Unevaluated properties"),
    ("finding", "F002", "title: T\nexperiments: [X001]\nconfidence: low\nextra: 1\n",
     "Unevaluated properties"),
    ("decision", "D002", "title: T\nstate: accepted\nextra: 1\n", "Unevaluated properties"),
    ("implref", "R001", "title: T\ndecision: D001\nstatus: open\nlinks: []\nextra: 1\n",
     "Unevaluated properties"),
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


def test_query_ref_and_type_both_filter(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "idea", "I002", "title: Other\ntheme: O\nquestion: Q?\nstatus: open\n")
    _write(root, "experiment", "X003", "title: Run 3\nidea: I002\nhypothesis: H\noutcome: refuted\n")

    def listed(*argv: str) -> list[str]:
        capsys.readouterr()
        assert rr.main(["--root", str(root), "query", *argv]) == 0
        return [line.split("\t")[0] for line in capsys.readouterr().out.splitlines()]

    assert listed("--ref", "I001") == ["X001", "X002"]
    assert listed("--ref", "I002") == ["X003"]
    assert listed("--type", "idea") == ["I001", "I002"]
    assert listed("--type", "experiment", "--ref", "I002") == ["X003"]
    assert listed("--type", "finding", "--ref", "I001") == []


def test_misplaced_record_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "idea", "I002", "title: T\ntheme: O\nquestion: Q\nstatus: open\n")
    (root / "records" / "idea" / "I002.md").rename(root / "records" / "finding" / "I002.md")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "expected at records/idea/I002.md, found I002.md" in capsys.readouterr().out


def test_wrong_filename_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "idea", "I002", "title: T\ntheme: O\nquestion: Q\nstatus: open\n")
    (root / "records" / "idea" / "I002.md").rename(root / "records" / "idea" / "I009.md")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "expected at records/idea/I002.md, found I009.md" in capsys.readouterr().out


def test_next_id_skips_gaps(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "experiment", "X005", "title: Run 5\nidea: I001\nhypothesis: H\noutcome: planned\n")
    assert rr.main(["--root", str(root), "next", "experiment"]) == 0
    assert capsys.readouterr().out.strip() == "X006"


def test_next_id(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "next", "experiment"]) == 0
    assert capsys.readouterr().out.strip() == "X003"


@pytest.mark.parametrize("argv", [["query"], ["next", "experiment"], ["export"]])
def test_commands_refuse_broken_register(root: Path, capsys: pytest.CaptureFixture[str],
                                         argv: list[str]) -> None:
    _write(root, "idea", "I002", "title: No theme\nquestion: Q\nstatus: open\n")
    assert rr.main(["--root", str(root), *argv]) == 1
    captured = capsys.readouterr()
    assert "I002: $: 'theme' is a required property" in captured.err
    assert captured.out == ""


def test_export_refuses_record_without_theme_status(root: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "idea", "I002", "title: No status\ntheme: O\nquestion: Q\n")
    assert rr.main(["--root", str(root), "export"]) == 1
    assert "'status' is a required property" in capsys.readouterr().err


def test_export_tree(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "export"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"from": "X001", "to": "I001", "rel": "idea"} in data["edges"]
    theme = next(t for t in data["themes"] if t["id"] == "O")
    assert theme["ideas"][0]["experiments"] == [
        {"id": "X001", "outcome": "refuted", "retracted": False},
        {"id": "X002", "outcome": "supported", "retracted": False}]
    assert next(r for r in data["records"] if r["id"] == "X001")["body"] == "## Result\nworse"


def test_export_ignores_decision_pointing_at_idea(root: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "decision", "D002", "title: Uses idea\nfindings: [F001]\nideas: [I001]\n"
                                     "state: accepted\n")
    assert rr.main(["--root", str(root), "export"]) == 0
    data = json.loads(capsys.readouterr().out)
    theme = next(t for t in data["themes"] if t["id"] == "O")
    assert theme["ideas"][0]["experiments"] == [
        {"id": "X001", "outcome": "refuted", "retracted": False},
        {"id": "X002", "outcome": "supported", "retracted": False}]


@pytest.mark.parametrize("clause", ["outcom=refuted", "noequals", "bogus=None"])
def test_query_rejects_bad_where_clause(root: Path, capsys: pytest.CaptureFixture[str],
                                        clause: str) -> None:
    assert rr.main(["--root", str(root), "query", "--where", clause]) == 2
    captured = capsys.readouterr()
    assert clause in captured.err
    assert captured.out == ""


def test_query_missing_field_matches_no_value(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "query", "--type", "experiment",
                    "--where", "retracted=None"]) == 0
    assert capsys.readouterr().out == ""


def test_query_rejects_unknown_ref(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "query", "--ref", "I009"]) == 2
    assert "no record I009" in capsys.readouterr().err


def test_citing_retracted_experiment_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "experiment", "X001",
           "title: Run 1\nidea: I001\nhypothesis: H\noutcome: refuted\nretracted: true\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "F001.experiments: X001 is retracted" in capsys.readouterr().out


def test_render_map(root: Path, capsys: pytest.CaptureFixture[str],
                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr.subprocess, "run", lambda *a, **k: rr.subprocess.CompletedProcess(a, 0, ""))
    assert rr.main(["--root", str(root), "render"]) == 0
    out = capsys.readouterr().out
    assert "```mermaid\nmindmap\n  root((Dokimi research))\n" \
           "    O[Traces, optimization and visualization]\n" \
           "      I001[I001 Compress - open]\n" \
           "        X001[X001 - Run 1]\n" \
           "        X002[X002 + Run 2]\n```" in out
    assert "| O | I001 | X001 | refuted | Run 1 |" in out
    assert "from commit `unknown`" in out
