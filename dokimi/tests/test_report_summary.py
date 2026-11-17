# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`dokimi report summary`: one summary over cli_output.v1 documents (#749)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dokimi import cli_output
from dokimi.report.summary import render, summarize

CLI = Path(__file__).resolve().parents[1] / "cli.py"


def _doc(tmp: Path, name: str, kind: str, **fields) -> Path:
    p = tmp / name
    p.write_text(json.dumps(cli_output.envelope(kind, **fields)), encoding="utf-8")
    return p


def test_rows_keep_each_documents_own_verdict(tmp_path):
    _doc(tmp_path, "a.json", "repo scan", verdict="pass", subjects=[{"subject": "s1", "verdict": "pass"}])
    _doc(tmp_path, "b.json", "memory lint", subjects=[{"subject": "s1", "verdict": "fail"},
                                                      {"subject": "s2", "verdict": None}])
    out = summarize([str(tmp_path)])
    states = {c["kind"]: c["state"] for c in out["checks"]}
    assert states == {"repo scan": "pass", "memory lint": "report_only"}
    assert out["overall"] == "pass"  # a null verdict is report-only, never promoted to a fail
    assert out["subjects"] == [
        {"subject": "s1", "verdicts": {"repo scan": "pass", "memory lint": "fail"}, "findings": []},
        {"subject": "s2", "verdicts": {"memory lint": None}, "findings": []}]


def test_missing_unreadable_and_foreign_inputs_stay_rows(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps({"schema_version": "plan.v1"}), encoding="utf-8")
    good = _doc(tmp_path, "ok.json", "repo scan", verdict="pass")
    out = summarize([str(tmp_path / "bad.json"), str(tmp_path / "other.json"), str(good),
                     str(tmp_path / "absent.json")])
    assert [c["state"] for c in out["checks"]] == ["invalid", "invalid", "pass", "not_run"]
    assert out["overall"] == "invalid"
    assert summarize([str(tmp_path / "absent.json")])["overall"] == "not_run"


def test_failed_status_and_cost_are_shown(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps(cli_output.error_document("arms run", "infra", "docker gone",
                                                      cost={"spent_usd": 1.5, "cap_usd": 5.0})), encoding="utf-8")
    out = summarize([str(p)])
    assert out["overall"] == "failed" and out["spent_usd"] == 1.5
    assert out["checks"][0]["reason"] == "docker gone"
    md = render(out, "markdown")
    assert "| arms run |" in md and "1.5 / 5.0" in md and "docker gone" in md


def test_annotations_escape_messages(tmp_path):
    p = _doc(tmp_path, "x.json", "catalog check", verdict="fail", reason="two\nlines 100%",
             subjects=[{"subject": "a::b", "verdict": "fail"}])
    lines = render(summarize([str(p)]), "annotations").splitlines()
    assert lines == ["::warning title=catalog check::fail: two%0Alines 100%25",
                     "::warning title=catalog check::a::b: fail"]


def test_markdown_escapes_pipes_and_marks_absent_subjects(tmp_path):
    _doc(tmp_path, "a.json", "k1", verdict="pass", reason="a|b", subjects=[{"subject": "s", "verdict": "pass"}])
    _doc(tmp_path, "b.json", "k2", verdict="pass")
    md = render(summarize([str(tmp_path)]), "markdown")
    assert "a\\|b" in md and "| s | pass | not in run |" not in md  # k2 has no subject column
    assert "| s | pass |" in md


def test_cli_exits_zero_on_a_failed_input_and_writes_out(tmp_path):
    out = tmp_path / "r" / "summary.json"
    done = subprocess.run([sys.executable, str(CLI), "report", "summary", str(tmp_path / "nope.json"),
                           "--format", "json", "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text())["checks"][0]["state"] == "not_run"


# --- the leaves that opt in (#749 layer 2) and their findings in the summary ---

import contextlib  # noqa: E402
import io  # noqa: E402
from unittest.mock import patch  # noqa: E402

from dokimi.artifacts import schema_errors  # noqa: E402
from dokimi.cli import main  # noqa: E402


def _main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.object(sys, "argv", ["dokimi", *argv]):
        try:
            main()
        except SystemExit as exc:
            code = int(exc.code) if isinstance(exc.code, int) else (1 if exc.code else 0)
    return code, out.getvalue(), err.getvalue()


def _catalog(tmp: Path, broken: bool = False) -> Path:
    root = tmp / "catalog"
    for name in ("alpha", "beta"):
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        text = f"---\nname: {name}\ndescription: Use for {name} tasks.\n---\nRun the checks.\n"
        if broken and name == "beta":
            text = "---\nname: [unclosed\n---\n"
        (skill / "SKILL.md").write_text(text, encoding="utf-8")
    return root


def test_catalog_check_json_is_one_document_with_a_subject_per_skill(tmp_path):
    code, out, _ = _main("catalog", "check", "--root", str(_catalog(tmp_path, broken=True)), "--kind", "skills", "--json")
    doc = json.loads(out)
    assert code == 1 and schema_errors("cli_output.v1", doc) == []
    assert (doc["kind"], doc["verdict"]) == ("catalog check", "fail")
    subjects = {s["subject"]: s for s in doc["subjects"]}
    assert subjects["alpha"]["verdict"] == "pass"
    assert subjects["beta"]["verdict"] == "fail" and subjects["beta"]["findings"][0]["level"] == "error"
    assert subjects["beta"]["findings"][0]["file"].endswith("SKILL.md")


def test_catalog_check_json_refuses_format_json(tmp_path):
    code, out, _ = _main("catalog", "check", "--root", str(_catalog(tmp_path)), "--json", "--format", "json")
    assert code == 2 and json.loads(out)["error"]["code"] == "input"


def test_catalog_eval_json_names_each_skill_and_keeps_its_receipts(tmp_path):
    root = _catalog(tmp_path)
    out_dir = tmp_path / "out"
    code, out, _ = _main("catalog", "eval", str(root), "--tier", "static", "--out-dir", str(out_dir), "--json")
    doc = json.loads(out)
    assert schema_errors("cli_output.v1", doc) == []
    assert doc["kind"] == "catalog eval" and doc["verdict"] in ("pass", "fail")
    assert code == (0 if doc["verdict"] == "pass" else 1)
    assert {s["subject"] for s in doc["subjects"]} == {"alpha", "beta"}
    assert (out_dir / "report.md").is_file()


def test_skill_routing_json_is_report_only_and_its_summary_goes_to_stderr(tmp_path):
    def skill(name, description):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n\n## A section here\n",
                                    encoding="utf-8")
        return str(d)
    code, out, err = _main("skill", "routing", skill("a", "profile a binary"), "--competitors",
                           skill("b", "tune a database"), "--queries-from-headings",
                           "--out", str(tmp_path / "r.json"), "--json")
    doc = json.loads(out)
    assert code == 0 and schema_errors("cli_output.v1", doc) == []
    assert doc["verdict"] is None and doc["subjects"] == [{"subject": "a", "verdict": None}]
    assert doc["summary"]["queries_self_derived"] is True and "triage only" in err
    assert doc["cost"]["spent_usd"] == 0.0


def test_findings_become_located_annotations_and_counts(tmp_path):
    doc = cli_output.envelope("catalog check", verdict="fail", subjects=[{"subject": "s", "verdict": "fail", "findings": [
        cli_output.finding("error", "load-error", "bad yaml", "skills/s/SKILL.md"),
        cli_output.finding("warn", "w", "x", "a,b:c.md", 3)]}])
    assert schema_errors("cli_output.v1", doc) == []
    p = tmp_path / "c.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    summary = summarize([str(p)])
    lines = render(summary, "annotations").splitlines()
    assert "::error file=skills/s/SKILL.md,title=catalog check load-error::s: bad yaml" in lines
    assert "::warning file=a%2Cb%3Ac.md,line=3,title=catalog check w::s: x" in lines
    assert not any(line.endswith("s: fail") for line in lines)  # the findings stand in for the bare verdict
    assert "| s | fail (2 findings) |" in render(summary, "markdown")


def test_summary_values_are_a_column(tmp_path):
    _doc(tmp_path, "a.json", "repo scan", summary={"claims": {"ok": 3, "fail": 1}, "files": 2, "big": [1]})
    md = render(summarize([str(tmp_path)]), "markdown")
    assert "claims.ok 3, claims.fail 1, files 2" in md and "dokimi_version" not in md and "big" not in md


def test_the_report_action_runs_the_renderer_and_parses_nothing():
    import yaml

    action = yaml.safe_load((CLI.parent.parent / "actions" / "report" / "action.yml").read_text(encoding="utf-8"))
    runs = "\n".join(s.get("run", "") for s in action["runs"]["steps"])
    assert "python" not in runs and "jq" not in runs.replace("--jq", "")
    for fmt in ("markdown", "json", "annotations"):
        assert f"dokimi report summary --format {fmt}" in runs
    assert '-- "$OUT"' in runs and "GITHUB_STEP_SUMMARY" in runs
    assert set(action["inputs"]) == {"out-dir", "comment", "github-token", "artifact-name"}


def test_repo_eval_renders_validate_and_scan_with_the_renderer():
    wf = (CLI.parent.parent / ".github" / "workflows" / "repo-eval.yml").read_text(encoding="utf-8")
    assert 'dokimi report summary "$RUNNER_TEMP/validate.json"' in wf
    assert 'dokimi report summary "$RUNNER_TEMP/repo-scan.json"' in wf
    assert '["counts"]' not in wf  # no leaf field read in YAML for these two


def test_repo_scan_subjects_put_each_failing_claim_at_its_file_and_line():
    from dokimi.commands.repo_scan import scan_subjects

    subjects = scan_subjects({"files": ["AGENTS.md", "CLAUDE.md"], "claims": [
        {"file": "AGENTS.md", "line": 4, "status": "fail_missing", "kind": "path", "claim": "src/x.py", "why": "absent"},
        {"file": "AGENTS.md", "line": 5, "status": "ok", "kind": "path", "claim": "src", "why": None}]})
    assert [s["subject"] for s in subjects] == ["AGENTS.md", "CLAUDE.md"]
    (f,) = subjects[0]["findings"]
    assert (f["file"], f["line"], f["code"]) == ("AGENTS.md", 4, "fail_missing") and subjects[1]["findings"] == []


# --- review round: what a bad input or a second document of one kind must not hide ---


def test_an_empty_directory_stays_a_not_run_row(tmp_path):
    good = _doc(tmp_path, "ok.json", "repo scan", verdict="pass")
    empty = tmp_path / "empty-results"
    empty.mkdir()
    out = summarize([str(empty), str(good)])
    assert [c["state"] for c in out["checks"]] == ["not_run", "pass"]
    assert out["checks"][0]["source"] == str(empty) and out["overall"] == "not_run"


def test_malformed_envelope_fields_are_invalid_rows_not_a_crash(tmp_path):
    _doc(tmp_path, "a.json", "ok", verdict="pass")
    docs = {"verdict": {"verdict": ["pass"]}, "status": {"status": "broken", "verdict": "pass"},
            "cost": {"cost": {"spent_usd": "1", "cap_usd": None}},
            "subjects": {"subjects": [{"subject": "s", "verdict": "maybe"}]}}
    for field, fields in docs.items():
        p = tmp_path / f"{field}.json"
        doc = cli_output.envelope("repo scan")
        doc.update(fields)
        p.write_text(json.dumps(doc), encoding="utf-8")
    out = summarize([str(tmp_path)])
    rows = {Path(c["source"]).stem: c for c in out["checks"]}
    assert rows["a"]["state"] == "pass" and out["overall"] == "invalid"
    for field in docs:
        assert (rows[field]["state"], rows[field]["reason"]) == ("invalid", f"{field} malformed")


def test_two_documents_of_one_kind_keep_each_subject_verdict(tmp_path):
    _doc(tmp_path, "a.json", "memory lint", subjects=[{"subject": "s1", "verdict": "fail"}])
    _doc(tmp_path, "b.json", "memory lint", subjects=[{"subject": "s1", "verdict": "pass"}])
    out = summarize([str(tmp_path)])
    assert out["subjects"][0]["verdicts"] == {"memory lint": "fail",
                                              f"memory lint ({tmp_path / 'b.json'})": "pass"}
    assert "::warning title=memory lint::s1: fail" in render(out, "annotations")
    assert f"memory lint ({tmp_path / 'b.json'})" in render(out, "markdown")


def test_markdown_subject_header_escapes_the_kind(tmp_path):
    _doc(tmp_path, "a.json", "scan|lint", subjects=[{"subject": "s", "verdict": "pass"}])
    md = render(summarize([str(tmp_path)]), "markdown")
    assert "| Subject | scan\\|lint |" in md and "| s | pass |" in md


def test_subject_annotation_title_escapes_its_delimiters(tmp_path):
    p = _doc(tmp_path, "a.json", "scan,file=other.py,line=1", subjects=[{"subject": "s", "verdict": "fail"}])
    assert render(summarize([str(p)]), "annotations").splitlines()[-1] == \
        "::warning title=scan%2Cfile=other.py%2Cline=1::s: fail"


def test_the_json_summary_conforms_to_its_schema(tmp_path):
    from dokimi.artifacts import SCHEMA_VERSIONS

    _doc(tmp_path, "a.json", "catalog check", verdict="fail", cost={"spent_usd": 0.5, "cap_usd": 1.0},
         subjects=[{"subject": "s", "verdict": "fail", "findings": [
             cli_output.finding("error", "x", "m", "SKILL.md", 3)]}])
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    doc = json.loads(render(summarize([str(tmp_path), str(tmp_path / "gone.json")]), "json"))
    assert SCHEMA_VERSIONS["report_summary"] == doc["schema_version"] == "report_summary.v1"
    assert schema_errors("report_summary.v1", doc) == []
    assert schema_errors("report_summary.v1", {**doc, "overall": "great"}) != []


def test_catalog_check_json_refuses_jsonl(tmp_path):
    code, out, _ = _main("catalog", "check", "--root", str(_catalog(tmp_path)), "--per-skill", "--jsonl",
                         str(tmp_path / "rows.jsonl"), "--json")
    assert code == 2 and "--jsonl" in json.loads(out)["error"]["message"]


def _routed_skill(tmp_path, dirname="a-dir", name="a"):
    d = tmp_path / dirname
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: profile a binary\n---\n\n# B\n\n## A section here\n",
                                encoding="utf-8")
    c = tmp_path / "b"
    c.mkdir()
    (c / "SKILL.md").write_text("---\nname: b\ndescription: tune a database\n---\n\n# B\n", encoding="utf-8")
    return d, c


_COST = {"usd": 0.25, "llm_calls": 2, "input_tokens": 9, "output_tokens": 2, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "replayed_calls": 0}
_ARM = {"selection_rate": 0.5, "selection_rate_among_answered": 0.5, "n_queries": 1, "n_calls": 2, "n_abstained": 0,
        "n_failed": 0, "repeats": 1, "model": "m", "noise": None}


def _routing_with_model(tmp_path, d, c, model_arm, artifact=None):
    class Client:
        pass
    with patch("dokimi.novelty.cost.client", return_value=Client()), \
            patch("dokimi.novelty.client.roll_up_cost", return_value=_COST), \
            patch("dokimi.novelty.routing.model_routing", return_value=model_arm):
        return _main("skill", "routing", artifact or str(d), "--competitors", str(c), "--queries-from-headings",
                     "--model", "m", "--max-usd", "1", "--out", str(tmp_path / "r.json"), "--json")


def test_skill_routing_json_names_the_loaded_skill_and_carries_the_model_rate(tmp_path, monkeypatch):
    d, c = _routed_skill(tmp_path)
    monkeypatch.chdir(d)
    code, out, _ = _routing_with_model(tmp_path, d, c, _ARM, artifact=".")
    doc = json.loads(out)
    assert code == 0 and schema_errors("cli_output.v1", doc) == []
    assert doc["subjects"] == [{"subject": "a", "verdict": None}]
    assert doc["summary"]["model_selection_rate"] == 0.5


def test_skill_routing_json_of_a_capped_run_keeps_its_document_and_its_exit(tmp_path):
    from dokimi.novelty.noise import partial_record

    d, c = _routed_skill(tmp_path)
    arm = {**_ARM, "partial": partial_record("spend cap reached", 1, 2, "spend_cap")}
    code, out, _ = _routing_with_model(tmp_path, d, c, arm)
    doc = json.loads(out)
    assert code == 1 and schema_errors("cli_output.v1", doc) == []
    assert (doc["status"], doc["reason"]) == ("budget_stopped", "spend cap reached")
    assert doc["cost"]["spent_usd"] == 0.25 and doc["artifacts"]["out"] == str(tmp_path / "r.json")


def test_the_report_action_uploads_after_a_failed_comment_and_finds_its_own_comment():
    import yaml

    steps = yaml.safe_load((CLI.parent.parent / "actions" / "report" / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    by_name = {s["name"]: s for s in steps}
    assert "always()" in by_name["Upload out-dir"]["if"]
    sticky = by_name["Sticky pull request comment"]["run"]
    assert "gh api user" in sticky and '.user.login == \\"$me\\"' in sticky
