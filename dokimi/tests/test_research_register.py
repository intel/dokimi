# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/research_register.py and the records in research/records/."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
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
    ("finding", "F002", "title: T\nexperiments: [X009]\nconfidence: low\n", "no record X009"),
    ("finding", "F002", "title: T\nexperiments: [I001]\nconfidence: low\n",
     "I001 is idea, want experiment"),
    ("finding", "F002", "title: T\nexperiments: [[X001]]\nconfidence: low\n",
     "F002.experiments: ['X001'] is not a record id"),
    ("finding", "F002", "title: T\nexperiments: [{a: 1}]\nconfidence: low\n",
     "F002.experiments: {'a': 1} is not a record id"),
    ("decision", "D002", "title: T\nfindings: [F009]\nstate: accepted\n", "no record F009"),
    ("decision", "D002", "title: T\nfindings: [X001]\nstate: accepted\n",
     "X001 is experiment, want finding"),
    ("decision", "D002", "title: T\nideas: [I009]\nstate: accepted\n", "no record I009"),
    ("decision", "D002", "title: T\nideas: [F001]\nstate: accepted\n",
     "F001 is finding, want idea"),
    ("decision", "D002", "title: T\nstate: superseded\nsuperseded_by: D009\n",
     "no record D009"),
    ("decision", "D002", "title: T\nstate: superseded\nsuperseded_by: F001\n",
     "F001 is finding, want decision"),
    ("implref", "R001", "title: T\ndecision: D009\nstatus: open\nlinks: []\n", "no record D009"),
    ("implref", "R001", "title: T\ndecision: F001\nstatus: open\nlinks: []\n",
     "F001 is finding, want decision"),
    ("implref", "R001", "title: T\ndecision: [[D001]]\nstatus: open\nlinks: []\n",
     "is not of type 'string'"),
])
def test_bad_record_fails(root: Path, capsys: pytest.CaptureFixture[str],
                          kind: str, rid: str, front: str, message: str) -> None:
    _write(root, kind, rid, front)
    assert rr.main(["--root", str(root), "check"]) == 1
    assert message in capsys.readouterr().out


def test_supersession_cycle_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "decision", "D002", "title: T\nstate: superseded\nsuperseded_by: D003\n")
    _write(root, "decision", "D003", "title: T\nstate: superseded\nsuperseded_by: D002\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    out = capsys.readouterr().out
    assert "D002: superseded_by chain loops back to D002" in out
    assert "D003: superseded_by chain loops back to D003" in out


@pytest.mark.parametrize("link", [
    "https://github.com/intel/x https://example.com/secret",
    "https://github.com/intel/../../other/repo",
    "https://github.com/intel/x/..",
    "https://github.com/intel/%2e%2e/other/repo",
    "https://github.com/intel/..\\\\other/repo",
    "https://github.com/intel/x\\nhttps://example.com/secret",
])
def test_link_outside_public_repos_fails(root: Path, capsys: pytest.CaptureFixture[str],
                                         link: str) -> None:
    _write(root, "idea", "I002",
           f"title: T\ntheme: O\nquestion: Q\nstatus: open\nlinks: [\"{link}\"]\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "I002: $.links[0]: " in capsys.readouterr().out


def test_public_repo_links_pass(root: Path) -> None:
    _write(root, "idea", "I002", "title: T\ntheme: O\nquestion: Q\nstatus: open\nlinks: ["
           "\"https://github.com/intel/dokimi/compare/main...napetrov:dokimi:research-records\", "
           "\"https://github.com/intel/dokimi/pulls?q=is:pr+label:research#top\", "
           "\"https://github.com/napetrov/dokimi/blob/main/docs/a_b-c.md#L1-L2\"]\n")
    assert rr.main(["--root", str(root), "check"]) == 0


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


@pytest.mark.parametrize("where", ["X003.md", "experiment/sub/X003.md", "experiment/X003.markdown"])
def test_file_outside_type_dir_fails(root: Path, capsys: pytest.CaptureFixture[str],
                                     where: str) -> None:
    path = root / "records" / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nid: X003\n---\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "not a record, records live at records/<type>/<id>.md" in capsys.readouterr().out


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


def test_query_finds_and_marks_retracted(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "experiment", "X002",
           "title: Run 2\nidea: I001\nhypothesis: H\noutcome: supported\nretracted: true\n")
    assert rr.main(["--root", str(root), "query", "--where", "retracted=true"]) == 0
    assert capsys.readouterr().out == "X002\texperiment\tsupported, retracted\tRun 2\n"
    assert rr.main(["--root", str(root), "query", "--where", "outcome=supported"]) == 0
    assert capsys.readouterr().out == "X002\texperiment\tsupported, retracted\tRun 2\n"


def test_query_rejects_unknown_ref(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(root), "query", "--ref", "I009"]) == 2
    assert "no record I009" in capsys.readouterr().err


def test_citing_retracted_experiment_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "experiment", "X001",
           "title: Run 1\nidea: I001\nhypothesis: H\noutcome: refuted\nretracted: true\n")
    assert rr.main(["--root", str(root), "check"]) == 1
    assert "F001.experiments: X001 is retracted" in capsys.readouterr().out


def test_export_marks_uncommitted_records(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "records")
    assert rr.main(["--root", str(root), "export"]) == 0
    clean = json.loads(capsys.readouterr().out)
    assert clean["commit"] and clean["dirty"] is False
    _write(root, "experiment", "X002",
           "title: Run 2\nidea: I001\nhypothesis: H\noutcome: refuted\n")
    assert rr.main(["--root", str(root), "export"]) == 0
    edited = json.loads(capsys.readouterr().out)
    assert (edited["commit"], edited["dirty"]) == (clean["commit"], True)
    assert rr.main(["--root", str(root), "render"]) == 0
    assert f"`{clean['commit']}` (with uncommitted changes)." in capsys.readouterr().out


def test_export_without_git_installed(root: Path, capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(rr.subprocess, "run", missing)
    assert rr.main(["--root", str(root), "export"]) == 0
    assert json.loads(capsys.readouterr().out)["commit"] is None


def test_export_and_render_mark_retracted(root: Path, capsys: pytest.CaptureFixture[str],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr.subprocess, "run", lambda *a, **k: rr.subprocess.CompletedProcess(a, 0, ""))
    _write(root, "experiment", "X003",
           "title: Run [3] a|b\nidea: I001\nhypothesis: H\noutcome: supported\nretracted: true\n")
    _write(root, "idea", "I002", "title: Bare (idea)\ntheme: O\nquestion: Q?\nstatus: open\n")
    assert rr.main(["--root", str(root), "export"]) == 0
    theme = next(t for t in json.loads(capsys.readouterr().out)["themes"] if t["id"] == "O")
    assert theme["ideas"][0]["experiments"][2] == {"id": "X003", "outcome": "supported",
                                                   "retracted": True}
    assert rr.main(["--root", str(root), "render"]) == 0
    out = capsys.readouterr().out
    assert "        X003[X003 retracted Run 3 a|b]\n      I002[I002 Bare idea - open]\n" in out
    assert "| O | I001 | X003 | supported, retracted | Run [3] a/b |" in out
    assert "| O | I002 | | | Bare (idea) |" in out


@pytest.mark.parametrize("command", ["export", "query", "render"])
def test_output_is_utf8_under_a_legacy_code_page(root: Path, command: str) -> None:
    (root / "records" / "experiment" / "X002.md").write_text(
        "---\nid: X002\ntype: experiment\ndate: 2026-10-14\ntitle: Run 2 → ±\nidea: I001\n"
        "hypothesis: H\noutcome: supported\n---\n", encoding="utf-8")
    script = PROJECT / "scripts" / "research_register.py"
    result = subprocess.run([sys.executable, str(script), "--root", str(root), command],
                            capture_output=True, check=False,
                            env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode("utf-8")
    if command == "export":
        assert next(r for r in json.loads(out)["records"] if r["id"] == "X002")["title"] == \
            "Run 2 → ±"
    else:
        assert "Run 2 → ±" in out


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


def test_render_flattens_multiline_title(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(root, "idea", "I002",
           "title: >\n  Folded title\n  over lines\ntheme: O\nquestion: Q?\nstatus: open\n")
    _write(root, "experiment", "X003",
           "title: >\n  Run\n  split\nidea: I002\nhypothesis: H\noutcome: refuted\n")
    assert rr.main(["--root", str(root), "render"]) == 0
    out = capsys.readouterr().out
    assert "I002[I002 Folded title over lines - open]" in out
    assert "X003[X003 - Run split]" in out
    assert all(line.count("[") == line.count("]") for line in out.splitlines())
    assert "| O | I002 | X003 | refuted | Run split |" in out


def _implref(root: Path, rid: str, decision: str, status: str, number: int) -> None:
    _write(root, "implref", rid, f"title: Build {rid}\ndecision: {decision}\nstatus: {status}\n"
                                 f"links: [https://github.com/intel/dokimi/pull/{number}]\n")


@pytest.fixture
def built(root: Path) -> Path:
    _write(root, "decision", "D002", "title: Do it\nfindings: [F001]\nstate: accepted\n")
    _write(root, "decision", "D003", "title: Done\nfindings: [F001]\nstate: accepted\n")
    _implref(root, "R001", "D002", "open", 1)
    _implref(root, "R002", "D003", "merged", 2)
    return root


def test_implementation_summary(built: Path) -> None:
    records, _ = rr.load(built)
    impl = rr.implementation(records)
    assert {d: i["summary"] for d, i in impl.items()} == {
        "D001": "none", "D002": "open", "D003": "merged"}
    assert impl["D002"]["implrefs"][0]["live"] is None


def test_gaps_offline(built: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rr.main(["--root", str(built), "gaps"]) == 0
    out = capsys.readouterr().out
    assert "Accepted decisions with nothing merged: 1\n  D002\topen\tDo it\t(R001 open)" in out
    assert "Implrefs not done: 1\n  R001\topen\tBuild R001" in out
    assert "--live asks GitHub" in out


def test_gaps_live_reports_drift_and_unlinked_issues(built: Path, capsys: pytest.CaptureFixture[str],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr, "github_state", lambda url: "merged")
    monkeypatch.setattr(rr, "open_issues", lambda repo: [{"number": 1, "title": "linked"},
                                                         {"number": 7, "title": "Loose end"}])
    assert rr.main(["--root", str(built), "gaps", "--live"]) == 0
    out = capsys.readouterr().out
    assert "Accepted decisions with nothing merged: 0" in out
    assert "R001\trecorded open, GitHub merged\tBuild R001" in out
    assert "Open intel/dokimi issues no record links to: 1\n  #7\tLoose end" in out


def test_github_state_without_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*a: object, **k: object) -> None:
        raise FileNotFoundError("gh")
    monkeypatch.setattr(rr.subprocess, "run", missing)
    assert rr.github_state("https://github.com/intel/dokimi/pull/1") is None
    assert rr.github_state("https://github.com/intel/dokimi/blob/main/x") is None
    assert rr.open_issues("intel/dokimi") == []


def test_render_html_embeds_data_safely(built: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(built, "experiment", "X009", "title: Run 9\nidea: I001\nhypothesis: H\noutcome: planned\n",
           "</script><script>alert(1)</script>")
    assert rr.main(["--root", str(built), "render", "--html"]) == 0
    page = capsys.readouterr().out
    assert page.count("</script>") == 2  # the data block and the code block, nothing from a body
    data = page.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    loaded = json.loads(data)
    assert loaded["implementation"]["D002"]["summary"] == "open"
    assert next(r for r in loaded["records"] if r["id"] == "X009")["body"].startswith("</script>")
    assert "/*DATA*/" not in page
