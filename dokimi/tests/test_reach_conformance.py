# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reach conformance suite, offline: which harnesses can run, how observed and documented states agree,
the matrix and its markdown page, and the fixtures against their expectations. Harness runs are faked."""

from __future__ import annotations

import importlib.resources
import importlib.util
import os
import tomllib
from pathlib import Path

import pytest

from dokimi import process
from dokimi.artifacts import load_artifact, save_artifact, schema_errors
from dokimi.repos import conformance as cf
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.receipt import build_receipt
from tests.test_repo_receipt import BUDGET, ROOT_ROW, _record, _reach, _tree

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "data" / "reach-conformance"
SCRIPT = PROJECT / "scripts" / "reach_conformance.py"
CODEX = cf.HARNESSES["codex"]
CLAUDE = cf.HARNESSES["claude-code"]
CASE_BLOCK = {"why": "docs", "files": {"AGENTS.md": {"cwd=.": "loaded"}, "CLAUDE.md": {"cwd=.": "not_loaded"}}}


def _expect(**blocks: dict) -> dict:
    return {"schema": "reach_conformance.v1", "cases": {"toy": {"summary": "toy case", **blocks}}}


def _found(name: str, search: str) -> str:
    return f"/usr/bin/{name}"


def _missing(name: str, search: str) -> None:
    return None


def _load_cli():
    spec = importlib.util.spec_from_file_location("reach_conformance_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _run(**fields) -> dict:
    base = {"harness": "codex", "version": "0.166.0", "model": "m", "case": "toy", "status": "ran",
            "reason": None, "scenarios": 1}
    return {**base, **fields}


def _cell(**fields) -> dict:
    base = {"harness": "codex", "version": "0.166.0", "case": "toy", "scenario": "cwd=.", "path": "AGENTS.md",
            "observed": "loaded", "documented": "loaded", "agreement": "agrees"}
    return {**base, **fields}


DIGEST = "a" * 64


def _matrix(runs=None, cells=None, generated_at="2026-10-31T00:00:00+00:00", digest=DIGEST) -> dict:
    return cf.build_matrix([_run()] if runs is None else runs, [_cell()] if cells is None else cells, digest,
                           generated_at=generated_at)


def test_unavailable_names_the_missing_piece():
    env = {"CODEX_API_KEY": "k"}
    assert cf.unavailable(CODEX, "gpt", env, which=_found) is None
    assert "no model" in cf.unavailable(CODEX, "", env, which=_found)
    assert "CODEX_API_KEY" in cf.unavailable(CODEX, "gpt", {}, which=_found)
    assert "not on PATH" in cf.unavailable(CODEX, "gpt", env, which=_missing)


def test_unavailable_looks_for_the_cli_on_the_path_the_run_gets(tmp_path, monkeypatch):
    run_bin, parent_bin = tmp_path / "run", tmp_path / "parent"
    for folder in (run_bin, parent_bin):
        folder.mkdir()
        (folder / "codex").write_text("#!/bin/sh\n", encoding="utf-8")
        (folder / "codex").chmod(0o755)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(parent_bin))  # this process's PATH has a codex; the run's does not
    assert "not on PATH" in cf.unavailable(CODEX, "gpt", {"CODEX_API_KEY": "k", "PATH": str(empty)})
    assert cf.unavailable(CODEX, "gpt", {"CODEX_API_KEY": "k", "PATH": str(run_bin)}) is None
    monkeypatch.setenv("PATH", str(empty))  # the other way round: the run's PATH has the codex, this process's has none
    assert cf.unavailable(CODEX, "gpt", {"CODEX_API_KEY": "k", "PATH": str(run_bin)}) is None


def test_unavailable_forwards_only_the_credentials_the_profile_forwards():
    # codex's profile forwards CODEX_API_KEY alone, so OPENAI_API_KEY would not reach the run
    assert cf.unavailable(CODEX, "gpt", {"OPENAI_API_KEY": "k"}, which=_found) is not None
    assert cf.unavailable(CLAUDE, "m", {"AWS_BEARER_TOKEN_BEDROCK": "t"}, which=_found) is None


@pytest.mark.parametrize(("observed", "documented", "alias", "expected"), [
    ("loaded", "loaded", False, "agrees"),
    ("not_loaded", "loaded", False, "disagrees"),
    ("loaded", "not_loaded", False, "disagrees"),
    ("loaded", "unknown", False, "no_claim"),
    ("unknown", "unknown", False, "no_claim"),
    ("unknown", "loaded", False, "inconclusive"),
    ("fetched", "loaded", False, "inconclusive"),
    ("loaded", "loaded", True, "alias"),
])
def test_agreement_rules(observed, documented, alias, expected):
    assert cf.agreement(observed, documented, alias) == expected


@pytest.mark.parametrize(("run", "scenario"), [
    ({"cwd": ".", "target": None}, "cwd=."),
    ({"cwd": "packages/app", "target": None}, "cwd=packages/app"),
    ({"cwd": ".", "target": "packages/app/REACH_PROBE.txt"}, "read=packages/app"),
])
def test_scenario_id_names_the_cwd_or_the_read_directory(run, scenario):
    assert cf.scenario_id(run) == scenario


def test_documented_is_unknown_unless_the_docs_name_the_file_and_scenario():
    expect = _expect(codex=CASE_BLOCK)
    assert cf.documented(expect, "toy", "codex", "CLAUDE.md", "cwd=.") == "not_loaded"
    assert cf.documented(expect, "toy", "codex", "CLAUDE.md", "cwd=sub") == "unknown"
    assert cf.documented(expect, "toy", "claude-code", "AGENTS.md", "cwd=.") == "unknown"
    assert cf.documented(expect, "other", "codex", "AGENTS.md", "cwd=.") == "unknown"


def test_cells_compare_each_source_with_the_documentation(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW]), BUDGET, tree)
    cells = cf.cells_of(receipt, "toy", _expect(**{"claude-code": {
        "why": "docs", "files": {"AGENTS.md": {"cwd=.": "not_loaded"}}}}))
    agents = next(c for c in cells if c["path"] == "AGENTS.md")
    assert agents == {"harness": "claude-code", "version": "2.1.0", "case": "toy", "scenario": "cwd=.",
                      "path": "AGENTS.md", "observed": "loaded", "documented": "not_loaded",
                      "agreement": "disagrees"}


def test_a_symbolic_link_is_an_alias_of_its_target(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n"})
    os.symlink("AGENTS.md", tmp_path / "CLAUDE.md")
    files = [{"path": "AGENTS.md", "head": "RC000001", "tail": "RC000002", "aliases": ["CLAUDE.md"]}]
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW], files=files), BUDGET,
                            tree)
    link = next(c for c in cf.cells_of(receipt, "toy", _expect()) if c["path"] == "CLAUDE.md")
    assert (link["agreement"], link["alias_of"], link["observed"]) == ("alias", "AGENTS.md", "loaded")


def test_build_matrix_counts_and_validates_against_the_schema():
    runs = [_run(), _run(harness="copilot", status="skipped", reason="no key", version="unknown")]
    cells = [_cell(), _cell(agreement="disagrees", observed="not_loaded", documented="loaded", path="CLAUDE.md"),
             _cell(agreement="alias", path="LINK.md", alias_of="AGENTS.md")]
    matrix = cf.build_matrix(runs, cells, DIGEST, generated_at="2026-10-31T00:00:00+00:00")
    assert matrix["summary"] == {"agrees": 1, "disagrees": 1, "inconclusive": 0, "no_claim": 0, "alias": 1,
                                 "runs": {"ran": 1, "errors": 0, "failed": 0, "skipped": 1}}
    assert schema_errors("reach_matrix.v1", matrix) == []


def _fake_reach(monkeypatch, receipt: dict, *, returncode: int = 0, timed_out: bool = False,
                write: bool = True) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv, *, timeout, env, **_):
        calls.append(list(argv))
        out = Path(argv[argv.index("--out") + 1])
        if write:
            out.mkdir(parents=True, exist_ok=True)
            save_artifact("effective_instructions", receipt, out / cf.RECEIPT_NAME)
        return process.Completed(argv=tuple(argv), returncode=returncode, timed_out=timed_out,
                                 stderr_bytes=b"boom")

    monkeypatch.setattr(cf.process, "run", fake_run)
    return calls


def _toy_fixture(tmp_path: Path) -> tuple[Path, dict]:
    fixture = tmp_path / "toy"
    tree = _tree(fixture, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n"})
    receipt = build_receipt(_reach([_record("AGENTS.md", "cwd=.", "loaded")], [ROOT_ROW]), BUDGET, tree)
    return fixture, receipt


def test_run_case_reads_the_receipt_the_run_wrote(tmp_path, monkeypatch):
    fixture, receipt = _toy_fixture(tmp_path)
    calls = _fake_reach(monkeypatch, receipt)
    expect = _expect(**{"claude-code": {"why": "docs", "files": {"AGENTS.md": {"cwd=.": "loaded"}}}})
    record, cells = cf.run_case(CLAUDE, "m", fixture, tmp_path / "guidance.yaml", tmp_path / "out", expect,
                                timeout=30, budget=0.5, env={})
    assert record == _run(harness="claude-code", version="2.1.0", model="m", status="ran",
                          scenarios=len(receipt["receipts"]))
    assert any(c["path"] == "AGENTS.md" and c["agreement"] == "agrees" for c in cells)
    argv = calls[0]
    assert argv[argv.index("--harness") + 1] == "claude-code" and argv[argv.index("--format") + 1] == "receipt"


def test_run_case_marks_a_run_with_a_failing_scenario_as_errors(tmp_path, monkeypatch):
    fixture, receipt = _toy_fixture(tmp_path)
    _fake_reach(monkeypatch, receipt, returncode=1)
    record, cells = cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", tmp_path / "out", _expect(),
                                timeout=30, budget=0.5, env={})
    assert (record["status"], record["reason"]) == ("errors", "exit 1: boom")
    assert cells


@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"write": False}, "exit 0"),
    ({"timed_out": True, "returncode": process.TIMEOUT_RC}, "timed out"),
])
def test_run_case_without_a_receipt_is_failed(tmp_path, monkeypatch, kwargs, reason):
    fixture, receipt = _toy_fixture(tmp_path)
    _fake_reach(monkeypatch, receipt, **kwargs)
    record, cells = cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", tmp_path / "out", _expect(),
                                timeout=30, budget=0.5, env={})
    assert record["status"] == "failed" and reason in record["reason"]
    assert cells == []


@pytest.mark.parametrize("text", [
    '{"schema_version": "effective_instructions.v1"}',  # fails the schema
    '{"schema_version": "effective_instructions.v1", "receipts": [',  # truncated JSON
    "[1, 2]",  # not an object
])
def test_run_case_with_a_malformed_receipt_is_failed_not_fatal(tmp_path, monkeypatch, text):
    fixture, _receipt = _toy_fixture(tmp_path)

    def fake_run(argv, *, timeout, env, **_):
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / cf.RECEIPT_NAME).write_text(text, encoding="utf-8")
        return process.Completed(argv=tuple(argv), returncode=0)

    monkeypatch.setattr(cf.process, "run", fake_run)
    record, cells = cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", tmp_path / "out", _expect(),
                                timeout=30, budget=0.5, env={})
    assert record["status"] == "failed" and record["reason"].startswith("invalid receipt")
    assert cells == []


def test_run_harness_skips_every_fixture_when_the_harness_cannot_run(tmp_path, monkeypatch):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("an unavailable harness must not be run")

    monkeypatch.setattr(cf.process, "run", must_not_run)
    matrix = cf.run_harness(CODEX, "gpt", FIXTURES, cf.load_expectations(FIXTURES / "expectations.yaml"),
                            tmp_path, env={}, which=_found)
    assert {r["case"] for r in matrix["runs"]} == set(cf.cases(FIXTURES))
    assert all(r["status"] == "skipped" and "CODEX_API_KEY" in r["reason"] for r in matrix["runs"])
    assert matrix["cells"] == [] and matrix["summary"]["runs"]["skipped"] == len(matrix["runs"])


def test_merge_joins_matrices_and_keeps_the_latest_time():
    first = _matrix(generated_at="2026-10-30T00:00:00+00:00")
    second = _matrix(runs=[_run(harness="claude-code", version="2.1.0")],
                     cells=[_cell(harness="claude-code", version="2.1.0")],
                     generated_at="2026-10-31T00:00:00+00:00")
    merged = cf.merge([first, second])
    assert len(merged["runs"]) == 2 and len(merged["cells"]) == 2
    assert merged["generated_at"] == "2026-10-31T00:00:00+00:00"
    assert schema_errors("reach_matrix.v1", merged) == []


def test_merge_refuses_a_harness_version_on_one_fixture_twice():
    with pytest.raises(cf.ConformanceError, match="codex 0.166.0 on toy"):
        cf.merge([_matrix(), _matrix()])


def test_merge_refuses_matrices_from_two_revisions_of_the_expectations():
    second = _matrix(runs=[_run(harness="claude-code")], cells=[_cell(harness="claude-code")], digest="b" * 64)
    with pytest.raises(cf.ConformanceError, match="2 revisions of the expectations"):
        cf.merge([_matrix(), second])


def test_expectations_digest_follows_the_content_not_the_key_order():
    expect = cf.load_expectations(FIXTURES / "expectations.yaml")
    reordered = dict(reversed(list(expect.items())))
    assert cf.expectations_digest(reordered) == cf.expectations_digest(expect)
    assert cf.expectations_digest({**expect, "note": "edited"}) != cf.expectations_digest(expect)
    matrix = cf.run_harness(CODEX, "", FIXTURES, expect, Path("unused"), env={}, which=_found)
    assert matrix["expectations_digest"] == cf.expectations_digest(expect)


def test_merge_keeps_two_failed_runs_of_one_harness():
    failed = _run(status="failed", reason="timed out", version="unknown", scenarios=0)
    merged = cf.merge([_matrix(runs=[failed], cells=[]), _matrix(runs=[failed], cells=[])])
    assert [r["status"] for r in merged["runs"]] == ["failed", "failed"]
    assert merged["cells"] == []


def test_merge_refuses_two_ran_runs_even_when_their_version_is_unknown():
    ran = _run(version="unknown")
    with pytest.raises(cf.ConformanceError, match="codex unknown on toy"):
        cf.merge([_matrix(runs=[ran]), _matrix(runs=[ran])])


def test_merge_refuses_nothing():
    with pytest.raises(cf.ConformanceError, match="nothing to merge"):
        cf.merge([])


def test_render_markdown_shows_observed_documented_and_links():
    matrix = _matrix(
        runs=[_run(), _run(harness="copilot", version="1.0.105", status="skipped", reason="a|b")],
        cells=[_cell(), _cell(path="CLAUDE.md", observed="not_loaded", documented="loaded", agreement="disagrees"),
               _cell(path="LINK.md", observed="loaded", documented="unknown", agreement="alias", alias_of="AGENTS.md")],
    )
    md = cf.render_markdown(matrix, {"toy": "toy case"})
    assert md.isascii()
    assert "## toy" in md and "toy case" in md
    assert "| file | scenario | codex 0.166.0 |" in md
    assert "| AGENTS.md | cwd=. | loaded |" in md
    assert "| CLAUDE.md | cwd=. | not_loaded (docs: loaded) |" in md
    assert "link, loaded" in md
    assert "a\\|b" in md
    assert "copilot 1.0.105" not in md.split("## toy")[1]


def test_expectations_match_the_fixture_tree():
    expect = cf.load_expectations(FIXTURES / "expectations.yaml")
    assert cf.check_expectations(expect, FIXTURES) == []
    assert {"large-file", "symlinked-file", "subtree-claude-shadow"} <= set(cf.cases(FIXTURES))


def test_check_expectations_reports_each_kind_of_drift(tmp_path):
    fixtures = tmp_path / "fixtures"
    _tree(fixtures / "toy", {"AGENTS.md": "a\n"})
    (fixtures / "extra").mkdir()
    expect = {"schema": "reach_conformance.v1", "cases": {
        "toy": {"summary": "s", "codex": {"why": "w", "files": {
            "CLAUDE.md": {"cwd=.": "loaded"}, "AGENTS.md": {"cwd=nope": "loaded", "cwd": "loaded"}}}},
        "gone": {"summary": "s"},
    }}
    problems = "\n".join(cf.check_expectations(expect, fixtures))
    assert "no expectations for fixture extra" in problems
    assert "expectations name no fixture gone" in problems
    assert "codex names CLAUDE.md, which the fixture does not hold" in problems
    assert "codex names scenario cwd=nope" in problems
    assert "codex names scenario cwd, which" in problems  # no "=": the empty directory is the fixture root, not a scenario


def test_load_expectations_rejects_another_schema(tmp_path):
    path = tmp_path / "expectations.yaml"
    path.write_text("schema: other.v1\ncases: {}\n", encoding="utf-8")
    with pytest.raises(cf.ConformanceError):
        cf.load_expectations(path)


def test_cli_check_passes_on_the_fixtures_and_fails_on_drift(tmp_path, capsys):
    assert cli.main(["check"]) == 0
    (tmp_path / "undeclared").mkdir()
    assert cli.main(["--fixtures", str(tmp_path), "--expectations", str(FIXTURES / "expectations.yaml"),
                     "check"]) == 1
    assert "no expectations for fixture undeclared" in capsys.readouterr().err


def test_cli_run_rejects_an_unknown_harness(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["run", "--harness", "nope=x", "--out", str(tmp_path)])


def test_cli_merge_writes_json_and_markdown(tmp_path):
    digest = cf.expectations_digest(cf.load_expectations(FIXTURES / "expectations.yaml"))
    left, right = tmp_path / "left.json", tmp_path / "right.json"
    cf.write_matrix(_matrix(digest=digest), left)
    cf.write_matrix(_matrix(runs=[_run(harness="claude-code", version="2.1.0")],
                            cells=[_cell(harness="claude-code", version="2.1.0")], digest=digest), right)
    out, md = tmp_path / "reach-matrix.json", tmp_path / "reach-matrix.md"
    assert cli.main(["merge", str(left), str(right), "--out", str(out), "--md", str(md)]) == 0
    assert load_artifact("reach_matrix", out)["summary"]["agrees"] == 2
    assert md.read_text(encoding="utf-8").startswith("# Reach matrix")


def test_cli_merge_refuses_matrices_from_another_revision_of_the_expectations(tmp_path, capsys):
    left = tmp_path / "left.json"
    cf.write_matrix(_matrix(), left)
    out, md = tmp_path / "reach-matrix.json", tmp_path / "reach-matrix.md"
    assert cli.main(["merge", str(left), "--out", str(out), "--md", str(md)]) == 1
    assert "another revision" in capsys.readouterr().err
    assert not out.exists() and not md.exists()


def test_merge_accepts_one_harness_version_on_two_fixtures():
    merged = cf.merge([_matrix(), _matrix(runs=[_run(case="other")], cells=[_cell(case="other")])])
    assert sorted(r["case"] for r in merged["runs"]) == ["other", "toy"]


def test_merge_accepts_two_versions_of_one_harness_on_one_fixture():
    merged = cf.merge([_matrix(), _matrix(runs=[_run(version="0.167.0")], cells=[_cell(version="0.167.0")])])
    assert sorted(r["version"] for r in merged["runs"]) == ["0.166.0", "0.167.0"]


def test_merge_refuses_two_errors_runs_of_one_harness_version():
    errors = _run(status="errors", reason="exit 1")
    with pytest.raises(cf.ConformanceError, match="codex 0.166.0 on toy"):
        cf.merge([_matrix(runs=[errors]), _matrix(runs=[errors])])


def test_merge_accepts_a_skipped_run_beside_a_ran_run_of_one_version():
    skipped = _run(status="skipped", reason="no model configured for this harness", scenarios=0)
    merged = cf.merge([_matrix(runs=[skipped], cells=[]), _matrix(runs=[_run()], cells=[_cell()])])
    assert sorted(r["status"] for r in merged["runs"]) == ["ran", "skipped"]


def test_render_markdown_keeps_a_multiline_reason_on_one_row():
    matrix = _matrix(runs=[_run(status="errors", reason="exit 1: a\nb | c")], cells=[])
    lines = cf.render_markdown(matrix).splitlines()
    assert "| toy | codex | 0.166.0 | m | errors | exit 1: a b \\| c |" in lines


def test_render_markdown_adds_no_column_for_a_failed_run():
    matrix = _matrix(runs=[_run(), _run(status="failed", version="unknown", reason="timed out", scenarios=0)])
    header = [line for line in cf.render_markdown(matrix).splitlines() if line.startswith("| file | scenario")]
    assert header == ["| file | scenario | codex 0.166.0 |"]


def test_run_case_does_not_read_a_receipt_an_earlier_run_left(tmp_path, monkeypatch):
    fixture, receipt = _toy_fixture(tmp_path)
    out = tmp_path / "out"
    save_artifact("effective_instructions", receipt, out / cf.RECEIPT_NAME)
    _fake_reach(monkeypatch, receipt, write=False)
    record, cells = cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", out, _expect(),
                                timeout=30, budget=0.5, env={})
    assert record["status"] == "failed" and cells == []


def test_cli_run_refuses_a_harness_given_twice_before_any_call(tmp_path, monkeypatch, capsys):
    def no_call(*args, **kwargs):
        raise AssertionError("run_harness must not be called")

    monkeypatch.setattr(cli.cf, "run_harness", no_call)
    out = tmp_path / "out"
    assert cli.main(["run", "--harness", "codex=a", "--harness", "codex=b", "--out", str(out)]) == 1
    assert "may be given once" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(("status", "exit_code"), [("failed", 1), ("errors", 0)])
def test_cli_run_exits_1_only_when_a_run_failed(tmp_path, monkeypatch, status, exit_code):
    run = _run(status=status, reason="exit 1", version="unknown" if status == "failed" else "0.166.0",
               scenarios=0 if status == "failed" else 1)
    monkeypatch.setattr(cli.cf, "run_harness", lambda *args, **kwargs: cf.build_matrix([run], [], DIGEST))
    assert cli.main(["run", "--harness", "codex=m", "--out", str(tmp_path)]) == exit_code
    assert (tmp_path / "codex.matrix.json").is_file()


def test_cli_merge_creates_both_output_directories_when_missing(tmp_path):
    digest = cf.expectations_digest(cf.load_expectations(FIXTURES / "expectations.yaml"))
    left = tmp_path / "left.json"
    cf.write_matrix(_matrix(digest=digest), left)
    out, md = tmp_path / "a" / "b" / "reach-matrix.json", tmp_path / "c" / "reach-matrix.md"
    assert cli.main(["merge", str(left), "--out", str(out), "--md", str(md)]) == 0
    assert out.is_file() and md.is_file()


def test_cli_reports_unusable_inputs_without_a_traceback(tmp_path, capsys):
    digest = cf.expectations_digest(cf.load_expectations(FIXTURES / "expectations.yaml"))
    left, bad = tmp_path / "left.json", tmp_path / "bad.json"
    cf.write_matrix(_matrix(digest=digest), left)
    bad.write_text("{}", encoding="utf-8")
    out, md = tmp_path / "reach-matrix.json", tmp_path / "reach-matrix.md"
    cases = [
        (["--expectations", str(tmp_path / "missing.yaml"), "check"], "missing.yaml"),
        (["merge", str(left), str(left), "--out", str(out), "--md", str(md)], "two matrices hold codex"),
        (["merge", str(bad), "--out", str(out), "--md", str(md)], "reach_matrix"),
    ]
    for argv, text in cases:
        assert cli.main(argv) == 1
        err = capsys.readouterr().err
        assert text in err and "Traceback" not in err


def test_run_case_hands_codex_the_suite_profile_with_its_provider(tmp_path, monkeypatch):
    fixture, receipt = _toy_fixture(tmp_path)
    calls = _fake_reach(monkeypatch, receipt)
    cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", tmp_path / "out", _expect(), timeout=30, budget=0.5, env={})
    argv = calls[0]
    assert argv[argv.index("--harness") + 1] == str(cf.CODEX_PROFILE)


def test_the_codex_suite_profile_authenticates_with_the_key_it_forwards(tmp_path):
    suite = load_profile(CODEX.profile_ref)
    builtin = load_profile("codex")
    assert "CODEX_API_KEY" in suite.env_allow
    assert (suite.invoke, suite.env_allow, suite.egress) == (builtin.invoke, builtin.env_allow, builtin.egress)
    suite.write_config(tmp_path, {"config_dir": tmp_path.as_posix(), "support_dir": tmp_path.as_posix()})
    config = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    provider = config["model_providers"][config["model_provider"]]  # the provider the run selects
    assert provider["env_key"] == "CODEX_API_KEY"
    assert provider["base_url"] == "https://api.openai.com/v1"


def test_the_codex_suite_profile_loads_through_the_package_resources():
    # The installed-wheel case: the file is found through the package, not through the source tree.
    shipped = importlib.resources.files("dokimi.repos") / "conformance_profiles" / "codex.yaml"
    with importlib.resources.as_file(shipped) as path:
        assert load_profile(path).name == "codex"
        assert path.read_bytes() == cf.CODEX_PROFILE.read_bytes()


def test_run_harness_gives_each_run_exactly_the_environment_it_is_given(tmp_path, monkeypatch):
    fixture, _receipt = _toy_fixture(tmp_path)
    seen: list[dict[str, str]] = []

    def fake_run(argv, *, timeout, env, **_):
        seen.append(env.resolve())
        return process.Completed(argv=tuple(argv), returncode=1)

    monkeypatch.setattr(cf.process, "run", fake_run)
    given = {"CODEX_API_KEY": "k", "PATH": "/usr/bin"}
    matrix = cf.run_harness(CODEX, "gpt", tmp_path, _expect(), tmp_path / "runs", env=given, which=_found)
    assert seen == [given]
    assert [r["status"] for r in matrix["runs"]] == ["failed"]


def test_run_case_fails_when_an_earlier_receipt_cannot_be_cleared(tmp_path, monkeypatch):
    fixture, receipt = _toy_fixture(tmp_path)
    out = tmp_path / "out"
    save_artifact("effective_instructions", receipt, out / cf.RECEIPT_NAME)
    calls = _fake_reach(monkeypatch, receipt)

    def refuse(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(cf.shutil, "rmtree", refuse)
    record, cells = cf.run_case(CODEX, "m", fixture, tmp_path / "g.yaml", out, _expect(),
                                timeout=30, budget=0.5, env={})
    assert record["status"] == "failed" and "cannot clear" in record["reason"]
    assert cells == [] and calls == []  # the old receipt is never read, and no probe runs


def test_merge_orders_matrices_by_the_instant_they_name_not_their_text():
    # 10:00 at +02:00 is 08:00 UTC, an hour before 09:00 UTC, though its text sorts higher
    earlier = _matrix(generated_at="2026-11-02T10:00:00+02:00")
    later = _matrix(runs=[_run(harness="claude-code", version="2.1.0")],
                    cells=[_cell(harness="claude-code", version="2.1.0")], generated_at="2026-11-02T09:00:00+00:00")
    assert cf.merge([earlier, later])["generated_at"] == "2026-11-02T09:00:00+00:00"


@pytest.mark.parametrize("stamp", ["2026-11-02 10:00", "not a time"])
def test_merge_refuses_a_timestamp_it_cannot_order(stamp):
    with pytest.raises(cf.ConformanceError, match="generated_at"):
        cf.merge([_matrix(generated_at=stamp)])


def test_render_markdown_escapes_every_table_field_not_only_the_reason():
    matrix = _matrix(runs=[_run(model="provider|model")], cells=[_cell(path="A|B.md", scenario="cwd=x|y")])
    lines = cf.render_markdown(matrix).splitlines()
    assert "| toy | codex | 0.166.0 | provider\\|model | ran | - |" in lines
    assert "| file | scenario | codex 0.166.0 |" in lines
    assert "| A\\|B.md | cwd=x\\|y | loaded |" in lines
