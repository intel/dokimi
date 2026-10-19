# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The journey trace refuses broken links and never lets missing evidence pass.

The catalog (docs/user-journeys/journeys.md) and the registry
(tests/outcomes/journeys.yaml) must agree with each other, with the tree and
with build_parser(); an enforced criterion passes only on claimed, passing
evidence, and a cli_scenario only when its test parsed the declared command.
"""

from __future__ import annotations

import json

import pytest
import yaml

from dokimi.cli import build_parser
from tests.outcomes import journeys, plugin

pytest_plugins = ["pytester"]

ROOT = build_parser()


def _catalog_text(extra_criteria: str = "") -> str:
    parts = []
    for n in range(1, 16):
        parts.append(f"## J{n} — Journey {n}\n\n#### UC-J{n:02d}-001 Case\n\n- **AC-J{n:02d}-001** Criterion {n}.\n")
    return "\n".join(parts) + extra_criteria


def _tree(tmp_path, catalog=None, criteria=None):
    """A repo root with a catalog, a registry and one doc, as the check reads them."""
    pages = tmp_path / "docs" / "user-journeys"
    pages.mkdir(parents=True)
    (pages / "journeys.md").write_text(catalog if catalog is not None else _catalog_text(), encoding="utf-8")
    (tmp_path / "doc.md").write_text("## Real heading\n", encoding="utf-8")
    registry = tmp_path / "journeys.yaml"
    registry.write_text(yaml.safe_dump({"schema": 1, "criteria": criteria or {}}), encoding="utf-8")
    return registry


def _evidence(results):
    """plugin._evidence over ``results`` instead of the session's own."""
    def lookup(evidence_id):
        saved, plugin._results = plugin._results, results
        try:
            return plugin._evidence(evidence_id)
        finally:
            plugin._results = saved
    return lookup


def _check(tmp_path, criteria, results=None, invocations=None, catalog=None):
    registry = _tree(tmp_path, catalog, criteria)
    return journeys.check(registry, tmp_path, {}, _evidence(results or {}), invocations or {}, plugin._anchors)


def _row(report, ac):
    return next(r for r in report["criteria"] if r["id"] == ac)


SCAN = {"id": "ev", "kind": "cli_scenario", "invokes": ["repo scan --out"], "stubs": []}


# ------------------------------------------------------------------ the real tree


def test_the_real_catalog_and_registry_have_no_problems():
    catalog, problems = journeys.parse_catalog(plugin.REPO_ROOT / journeys.CATALOG)
    assert problems == []
    registry = yaml.safe_load((plugin.CONTRACT_DIR / "journeys.yaml").read_text(encoding="utf-8"))
    contracts = {json.loads(p.read_text())["id"]: json.loads(p.read_text()) for p in plugin.CONTRACT_DIR.glob("*.json")}
    assert journeys.static_problems(registry, catalog, plugin.REPO_ROOT, contracts, plugin._anchors, ROOT) == []
    pages = sorted((plugin.REPO_ROOT / journeys.CATALOG).parent.glob("*.md"))
    assert journeys.dangling_ids(pages, catalog) == []


def test_the_real_catalog_keeps_the_fifteen_journeys_and_novelty_under_j14():
    catalog, _ = journeys.parse_catalog(plugin.REPO_ROOT / journeys.CATALOG)
    assert list(catalog["journeys"]) == journeys.JOURNEYS
    assert not any("novelty" in j["title"].lower() for j in catalog["journeys"].values())
    assert any("novelty" in catalog["use_cases"][uc]["title"].lower() for uc in catalog["journeys"]["J14"]["use_cases"])


def test_every_enforced_criterion_names_evidence_some_test_claims():
    """The ids are spelled as the markers spell them; a typo would only show at session end."""
    registry = yaml.safe_load((plugin.CONTRACT_DIR / "journeys.yaml").read_text(encoding="utf-8"))
    ids = {e["id"] for entry in registry["criteria"].values() if entry.get("enforce") for e in entry["evidence"]}
    source = "\n".join(p.read_text(encoding="utf-8") for p in (plugin.REPO_ROOT / "tests").glob("test_*.py"))
    # Match both single and double quotes: f'"{i}"' or f"'{i}'" or other syntaxes
    missing = [i for i in ids if f'"{i}"' not in source and f"'{i}'" not in source]
    assert sorted(missing) == []


# ------------------------------------------------------------------ catalog problems


def test_a_missing_journey_is_a_problem(tmp_path):
    text = _catalog_text().replace("## J7 — Journey 7", "## Not a journey")
    (tmp_path / "j.md").write_text(text, encoding="utf-8")
    _, problems = journeys.parse_catalog(tmp_path / "j.md")
    assert any("journeys not defined: J7" in p for p in problems)


def test_a_duplicate_or_misplaced_criterion_is_a_problem(tmp_path):
    text = _catalog_text("\n- **AC-J14-001** Again.\n- **AC-J03-009** Under J14.\n")
    (tmp_path / "j.md").write_text(text, encoding="utf-8")
    _, problems = journeys.parse_catalog(tmp_path / "j.md")
    assert any("AC-J14-001 defined twice" in p for p in problems)
    assert any("AC-J03-009 is not under a use case of J3" in p for p in problems)


def test_a_criterion_inside_a_code_fence_is_not_defined(tmp_path):
    text = _catalog_text("\n```\n- **AC-J14-002** Example only.\n```\n")
    (tmp_path / "j.md").write_text(text, encoding="utf-8")
    catalog, _ = journeys.parse_catalog(tmp_path / "j.md")
    assert "AC-J14-002" not in catalog["criteria"]


def test_an_id_a_page_mentions_but_the_catalog_lacks_is_dangling(tmp_path):
    (tmp_path / "j.md").write_text(_catalog_text("\nSee AC-J04-099.\n"), encoding="utf-8")
    catalog, _ = journeys.parse_catalog(tmp_path / "j.md")
    found = journeys.dangling_ids([tmp_path / "j.md"], catalog)
    assert len(found) == 1 and found[0].endswith(": AC-J04-099 is not defined in the catalog")


# ------------------------------------------------------------------ registry problems


@pytest.mark.parametrize("entry, problem", [
    ({"delivery": "done"}, "delivery must be one of"),
    ({"delivery": "proposed", "enforce": True, "evidence": [{"id": "e", "kind": "unit", "stubs": []}]},
     "an enforced criterion is partial or available"),
    ({"delivery": "available", "enforce": True, "evidence": [{"id": "e", "kind": "live_agent"}]},
     "needs evidence a pytest session produces"),
    ({"delivery": "available", "surfaces": ["repo nosuch"]}, "unknown command: repo nosuch"),
    ({"delivery": "available", "surfaces": ["repo scan --no-such-flag"]}, "unknown flag: repo scan --no-such-flag"),
    ({"delivery": "available", "surfaces": ["repo"]}, "not a leaf command: repo"),
    ({"delivery": "available", "evidence": [{"id": "e", "kind": "cli_scenario", "stubs": []}]},
     "a cli_scenario names the surface it invokes"),
    ({"delivery": "available", "evidence": [{"id": "e", "kind": "unit", "invokes": ["repo scan"], "stubs": []}]},
     "only a cli_scenario declares invokes"),
    ({"delivery": "available", "evidence": [{"id": "e", "kind": "integration"}]}, "state its stubs"),
    ({"delivery": "available", "evidence": [{"id": "e", "kind": "e2e", "stubs": []}]}, "kind must be one of"),
    ({"delivery": "available", "evidence": [{"kind": "unit", "stubs": []}]}, "must have a non-empty id"),
    ({"delivery": "available", "evidence": [{"id": "", "kind": "unit", "stubs": []}]}, "must have a non-empty id"),
    ({"delivery": "available", "refs": ["missing.py"]}, "missing path: missing.py"),
    ({"delivery": "available", "refs": ["doc.md#renamed"]}, "missing anchor: doc.md#renamed"),
    ({"delivery": "available", "outcome": "OUTCOME-NONE"}, "no outcome contract OUTCOME-NONE"),
    ({"delivery": "available", "decision_ref": "see chat"}, "decision_ref is an issue or PR URL"),
])
def test_a_broken_entry_is_an_error(tmp_path, entry, problem):
    report = _check(tmp_path, {"AC-J04-001": entry})
    assert report["verdict"] == "error"
    assert any(problem in p for p in report["problems"]), report["problems"]


def test_a_criterion_the_catalog_does_not_define_is_an_error(tmp_path):
    report = _check(tmp_path, {"AC-J04-777": {"delivery": "proposed"}})
    assert report["verdict"] == "error"
    assert any("AC-J04-777: not defined" in p for p in report["problems"])


def test_evidence_outside_the_named_outcome_contract_is_an_error(tmp_path):
    registry = _tree(tmp_path, criteria={"AC-J04-001": {
        "delivery": "available", "outcome": "C", "evidence": [{"id": "other", "kind": "unit", "stubs": []}]}})
    report = journeys.check(registry, tmp_path, {"C": {"evidence_ids": ["ev"]}}, _evidence({"other": ["passed"]}),
                            {}, plugin._anchors)
    assert any("other is not an evidence id of C" in p for p in report["problems"])


def test_evidence_with_missing_id_is_rejected_even_without_outcome_contract(tmp_path):
    """Missing ID in evidence should be caught during validation, not silently discarded."""
    registry = _tree(tmp_path, criteria={"AC-J04-001": {
        "delivery": "available", "evidence": [
            {"id": "valid", "kind": "unit", "stubs": []},
            {"kind": "unit", "stubs": []}  # missing id
        ]}})
    report = journeys.check(registry, tmp_path, {}, _evidence({"valid": ["passed"]}), {}, plugin._anchors)
    assert report["verdict"] == "error"
    assert any("must have a non-empty id" in p for p in report["problems"])


def test_a_real_anchor_and_surface_resolve(tmp_path):
    entry = {"delivery": "available", "refs": ["doc.md#real-heading"], "surfaces": ["repo scan --fail-on-fail"]}
    assert _check(tmp_path, {"AC-J04-001": entry})["problems"] == []


# ------------------------------------------------------------------ verdicts


def _enforced(*evidence):
    return {"AC-J04-001": {"delivery": "available", "enforce": True, "evidence": list(evidence)}}


UNIT = {"id": "ev", "kind": "unit", "stubs": []}


@pytest.mark.parametrize("outcomes, verdict", [
    (None, "error"),
    (["passed", "failed"], "fail"),
    (["skipped", "skipped"], "inconclusive"),
    (["passed"], "pass"),
])
def test_enforced_evidence_passes_only_when_claimed_and_passing(tmp_path, outcomes, verdict):
    report = _check(tmp_path, _enforced(UNIT), results={"ev": outcomes} if outcomes else {})
    assert report["verdict"] == verdict
    assert _row(report, "AC-J04-001")["verdict"] == verdict


def test_an_unenforced_criterion_with_unwritten_tests_is_not_run_and_blocks_nothing(tmp_path):
    report = _check(tmp_path, {"AC-J04-001": {"delivery": "partial", "evidence": [UNIT]}})
    assert report["verdict"] == "pass"
    assert _row(report, "AC-J04-001")["verdict"] == "not_run"
    assert "AC-J04-001" in report["unverified"]


def test_untracked_criteria_are_listed_as_unverified(tmp_path):
    report = _check(tmp_path, {})
    assert report["verdict"] == "pass"
    assert len(report["unverified"]) == 15 and not any(r["tracked"] for r in report["criteria"])


def test_a_cli_scenario_that_never_parsed_its_command_does_not_pass(tmp_path):
    report = _check(tmp_path, _enforced(SCAN), results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "reach", "x", "--out", "o"]]})
    row = _row(report, "AC-J04-001")
    assert report["verdict"] == "error" and row["verdict"] == "error"
    assert row["evidence"][0]["invokes"] == {"repo scan --out": False}


def test_a_cli_scenario_needs_every_declared_flag(tmp_path):
    report = _check(tmp_path, _enforced(SCAN), results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "scan", "src@HEAD"]]})
    assert report["verdict"] == "error"


def test_a_cli_scenario_that_parsed_its_command_passes(tmp_path):
    report = _check(tmp_path, _enforced(SCAN), results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "scan", "src@HEAD", "--out=r.json"]]})
    assert report["verdict"] == "pass"


def test_an_alias_reaches_the_same_command():
    assert journeys.invoked(ROOT, "library list", [["product", "list"]])


def test_the_report_states_its_provenance_and_stubs(tmp_path):
    stubbed = {**UNIT, "stubs": ["model client"]}
    report = _check(tmp_path, _enforced(stubbed), results={"ev": ["passed"]})
    assert {"commit", "run_id", "date", "scope"} <= set(report["provenance"])
    text = journeys.markdown(report)
    assert "stubs: model client" in text and "not a completed real-user journey" in text
    assert "14 of 15 documented criteria are not enforced or have no passing evidence" in text


# ------------------------------------------------------------------ through a real session


def _session(pytester, monkeypatch, criteria, test_source):
    root = pytester.path
    contracts = root / "contracts"
    contracts.mkdir()
    (contracts / "c.json").write_text(json.dumps({"id": "C", "evidence_ids": ["base"]}), encoding="utf-8")
    registry = _tree(root, criteria=criteria)
    registry.rename(contracts / "journeys.yaml")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REPO_ROOT", root)
    monkeypatch.setattr(plugin, "_results", {})
    monkeypatch.setattr(plugin, "_invocations", {})
    pytester.makepyfile("import pytest\nfrom dokimi.cli import build_parser\n\n"
                        "@pytest.mark.outcome('base')\ndef test_base():\n    assert True\n\n" + test_source)
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--outcomes-report=o.json",
                                "--journeys-report=j.json")
    return result, json.loads((root / "j.json").read_text())


def test_a_session_records_the_argv_a_marked_test_parsed(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "@pytest.mark.outcome('ev')\ndef test_scan():\n"
                              "    build_parser().parse_args(['repo', 'scan', 'x@HEAD', '--out', 'r.json'])\n")
    assert result.ret == 0 and report["verdict"] == "pass"
    assert (pytester.path / "j.md").read_text().startswith("# Journey coverage")


def test_a_command_named_only_in_a_string_is_not_an_invocation(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "@pytest.mark.outcome('ev')\ndef test_scan():\n"
                              "    assert 'repo scan --out' in 'python cli.py repo scan --out r.json'\n")
    assert result.ret == 2 and report["verdict"] == "error"


def test_an_argv_the_parser_rejects_is_not_an_invocation(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "@pytest.mark.outcome('ev')\ndef test_scan():\n"
                              "    with pytest.raises(SystemExit):\n"
                              "        build_parser().parse_args(['repo', 'scan', '--out', 'r', '--bogus'])\n")
    assert result.ret == 2 and report["verdict"] == "error"


def test_an_argv_with_unknown_arguments_after_required_args_is_not_an_invocation(pytester, monkeypatch):
    """parse_known_args succeeds even with unknown args; parse_args later rejects them."""
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "@pytest.mark.outcome('ev')\ndef test_scan():\n"
                              "    with pytest.raises(SystemExit):\n"
                              "        build_parser().parse_args(['repo', 'scan', 'x@HEAD', '--out', 'r.json', '--bogus'])\n")
    assert result.ret == 2 and report["verdict"] == "error"
    # The argv should not be recorded even though parse_known_args succeeded
    assert "repo" not in str(report.get("cli_invocations", []))


def test_an_all_skipped_enforced_criterion_makes_the_session_inconclusive(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(UNIT),
                              "@pytest.mark.outcome('ev')\ndef test_x():\n    pytest.skip('no docker')\n")
    assert result.ret == 3 and report["verdict"] == "inconclusive"


def test_an_enforced_surface_no_scenario_invokes_is_a_problem(tmp_path):
    entry = _enforced(SCAN)
    entry["AC-J04-001"]["surfaces"] = ["repo scan --fail-on-fail"]
    report = _check(tmp_path, entry, results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "scan", "src@HEAD", "--out=r.json"]]})
    assert report["verdict"] == "error"
    assert any("no cli_scenario invokes the surface 'repo scan --fail-on-fail'" in p
               for p in report["problems"])


def test_an_enforced_surface_a_scenario_declares_passes(tmp_path):
    entry = _enforced({**SCAN, "invokes": ["repo scan --out", "repo scan --fail-on-fail"]})
    entry["AC-J04-001"]["surfaces"] = ["repo scan --fail-on-fail"]
    report = _check(tmp_path, entry, results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "scan", "s", "--out=r.json"],
                                        ["repo", "scan", "s", "--fail-on-fail"]]})
    assert report["problems"] == [] and report["verdict"] == "pass"


def test_evidence_a_session_cannot_produce_does_not_unverify_a_passing_criterion(tmp_path):
    manual = {"id": "m1", "kind": "recorded_manual"}
    report = _check(tmp_path, _enforced(UNIT, manual), results={"ev": ["passed"]})
    row = _row(report, "AC-J04-001")
    assert row["verdict"] == "pass"
    assert [(e["id"], e["verdict"]) for e in row["evidence"]] == [("ev", "pass"), ("m1", "not_run")]
    assert "AC-J04-001" not in report["unverified"]
    assert next(j for j in report["journeys"] if j["id"] == "J4")["passed"] == 1


@pytest.mark.parametrize("path, ok", [("j.json", True), ("sub/dir/j.json", True), ("report.md", False)])
def test_the_journeys_report_must_be_json_and_its_directory_is_created(pytester, monkeypatch, path, ok):
    criteria = _enforced(UNIT)
    root = pytester.path
    contracts = root / "contracts"
    contracts.mkdir()
    registry = _tree(root, criteria=criteria)
    registry.rename(contracts / "journeys.yaml")
    monkeypatch.setattr(plugin, "CONTRACT_DIR", contracts)
    monkeypatch.setattr(plugin, "REPO_ROOT", root)
    monkeypatch.setattr(plugin, "_results", {})
    monkeypatch.setattr(plugin, "_invocations", {})
    pytester.makepyfile("import pytest\n\n@pytest.mark.outcome('ev')\ndef test_x():\n    assert True\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes",
                                "--outcomes-report=o.json", f"--journeys-report={path}")
    if ok:
        assert json.loads((root / path).read_text())["schema"] == "journey-coverage/1"
        assert (root / path).with_suffix(".md").is_file()
    else:
        assert result.ret != 0 and "must be a .json path" in result.stderr.str() + result.stdout.str()


# ------------------------------------------------------------------ options as argparse reads them


def test_a_short_alias_counts_as_its_long_option():
    assert journeys.invoked(ROOT, "repo run --jobs", [["repo", "run", "src", "-j", "2"]])
    assert journeys.invoked(ROOT, "repo run -j", [["repo", "run", "src", "--jobs=2"]])


def test_an_abbreviation_argparse_accepts_counts_as_its_option():
    assert journeys.invoked(ROOT, "repo tasks check --task", [["repo", "tasks", "check", "src", "--ta", "x"]])


def test_text_after_the_separator_is_not_an_option():
    # argparse reads this as source='--fail-on-fail' with fail_on_fail=False.
    assert not journeys.invoked(ROOT, "repo scan --fail-on-fail", [["repo", "scan", "--", "--fail-on-fail"]])
    assert "not '--'" in journeys.surface_problem(ROOT, "repo scan -- --fail-on-fail")


def test_options_sharing_a_dest_stay_two_options():
    import argparse

    root = argparse.ArgumentParser(prog="dokimi")
    cmd = root.add_subparsers(dest="cmd").add_parser("c")
    cmd.add_argument("--x", dest="v", action="store_true")
    cmd.add_argument("--no-x", dest="v", action="store_false")
    assert not journeys.invoked(root, "c --x", [["c", "--no-x"]])
    assert journeys.invoked(root, "c --x", [["c", "--x"]])


def test_help_and_unknown_options_are_still_reported():
    assert journeys.surface_problem(ROOT, "repo scan --no-such-flag") == "unknown flag: repo scan --no-such-flag"
    assert journeys.surface_problem(ROOT, "repo scan -h") is None
    assert journeys.surface_problem(ROOT, "repo scan --fail-on") is None  # an unambiguous abbreviation


# ------------------------------------------------------------------ verdict rules each mutation would break


def test_an_enforced_criterion_with_one_unclaimed_id_is_an_error(tmp_path):
    report = _check(tmp_path, _enforced(UNIT, {**UNIT, "id": "unwritten"}), results={"ev": ["passed"]})
    assert _row(report, "AC-J04-001")["verdict"] == "error" and report["verdict"] == "error"


def test_a_failing_unenforced_criterion_gates_nothing(tmp_path):
    report = _check(tmp_path, {"AC-J04-001": {"delivery": "partial", "evidence": [UNIT]}}, results={"ev": ["failed"]})
    assert _row(report, "AC-J04-001")["verdict"] == "fail"
    assert report["verdict"] == "pass"


def test_a_passing_unenforced_criterion_is_still_unverified(tmp_path):
    report = _check(tmp_path, {"AC-J04-001": {"delivery": "partial", "evidence": [UNIT]}}, results={"ev": ["passed"]})
    assert _row(report, "AC-J04-001")["verdict"] == "pass"
    assert "AC-J04-001" in report["unverified"]
    assert next(j for j in report["journeys"] if j["id"] == "J4")["passed"] == 0


def test_a_cli_scenario_needs_every_declared_invoke(tmp_path):
    both = {**SCAN, "invokes": ["repo scan --out", "repo reach --out"]}
    report = _check(tmp_path, _enforced(both), results={"ev": ["passed"]},
                    invocations={"ev": [["repo", "scan", "s", "--out=r.json"]]})
    assert _row(report, "AC-J04-001")["verdict"] == "error"


def test_a_missing_catalog_is_a_problem_and_the_report_is_still_built(tmp_path):
    registry = _tree(tmp_path, criteria=_enforced(UNIT))
    (tmp_path / journeys.CATALOG).unlink()
    report = journeys.check(registry, tmp_path, {}, _evidence({"ev": ["passed"]}), {}, plugin._anchors)
    assert report["verdict"] == "error"
    assert any("cannot read" in p for p in report["problems"])
    assert journeys.markdown(report).startswith("# Journey coverage")


# ------------------------------------------------------------------ capture, through a real session


def test_an_argv_parsed_on_a_worker_thread_is_recorded(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "import threading\nfrom concurrent.futures import ThreadPoolExecutor\n\n"
                              "@pytest.mark.outcome('ev')\ndef test_scan():\n"
                              "    argv = ['repo', 'scan', 'x@HEAD', '--out', 'r.json']\n"
                              "    with ThreadPoolExecutor(1) as ex:\n"
                              "        ex.submit(build_parser().parse_args, argv).result()\n")
    assert result.ret == 0 and report["verdict"] == "pass"


def test_an_invocation_made_by_a_skipped_test_does_not_count(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(SCAN),
                              "@pytest.mark.outcome('ev')\ndef test_passes():\n    assert True\n\n"
                              "@pytest.mark.outcome('ev')\ndef test_skips():\n"
                              "    build_parser().parse_args(['repo', 'scan', 'x@HEAD', '--out', 'r.json'])\n"
                              "    pytest.skip('no docker')\n")
    assert result.ret == 2 and report["verdict"] == "error"


def test_a_failure_outranks_an_all_skipped_criterion(pytester, monkeypatch):
    criteria = {"AC-J04-001": _enforced(UNIT)["AC-J04-001"],
                "AC-J05-001": {"delivery": "available", "enforce": True, "evidence": [{**UNIT, "id": "sk"}]}}
    result, report = _session(pytester, monkeypatch, criteria,
                              "@pytest.mark.outcome('ev')\ndef test_x():\n    assert False\n\n"
                              "@pytest.mark.outcome('sk')\ndef test_y():\n    pytest.skip('no docker')\n")
    assert report["verdict"] == "fail" and result.ret == 1


def test_a_fixture_error_in_one_claimed_test_fails_the_evidence(pytester, monkeypatch):
    result, report = _session(pytester, monkeypatch, _enforced(UNIT),
                              "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n\n"
                              "@pytest.mark.outcome('ev')\ndef test_ok():\n    assert True\n\n"
                              "@pytest.mark.outcome('ev')\ndef test_bad(broken):\n    assert True\n")
    assert _row(report, "AC-J04-001")["verdict"] == "fail" and result.ret == 1


def test_a_journeys_report_that_is_not_json_fails_before_the_run(pytester):
    pytester.makepyfile("def test_x():\n    raise AssertionError('should not run')\n")
    result = pytester.runpytest("-p", "tests.outcomes.plugin", "--outcomes", "--journeys-report=j.txt")
    assert result.ret == 4 and "must be a .json path" in result.stderr.str()
    assert "should not run" not in result.stdout.str()


def test_a_failure_outranks_inconclusive_when_contracts_and_journeys_combine():
    assert plugin._worst(["inconclusive", "fail"]) == "fail"
    assert plugin._worst(["pass", "inconclusive"]) == "inconclusive"
